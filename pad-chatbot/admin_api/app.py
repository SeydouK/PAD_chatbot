"""API d'administration du chatbot PAD : documents du RAG et connexions aux bases de données.

Lancer (depuis pad-chatbot) :   python -m admin_api          (écoute sur 127.0.0.1:8010)
Créer un admin :                python -m admin_api.create_admin NOM
Ne pas l'exposer sans HTTPS (reverse proxy) : les mots de passe passent dans les requêtes.
"""
import io
import ipaddress
import os
import re
import socket
import subprocess
import threading
import time
import unicodedata
from functools import wraps
from pathlib import Path

from urllib.parse import urlparse

from flask import Flask, g, jsonify, request
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL

import sys
RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
from actions import store                      # noqa: E402
import export_db                               # noqa: E402
import import_api                              # noqa: E402

DOCS = Path(os.getenv("PAD_DOCS_DIR") or RACINE / "docs")
UPLOADS = DOCS / "uploads"
MAX_OCTETS = 10 * 1024 * 1024
EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}
MIN_MOTS = 30

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_OCTETS + 1024 * 1024
app.json.ensure_ascii = False


# ---------- sécurité ----------
@app.after_request
def entetes(r):
    r.headers["Cache-Control"] = "no-store"
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r


def erreur(msg: str, code: int = 400):
    return jsonify({"erreur": msg}), code


def admin_requis(f):
    @wraps(f)
    def wrap(*a, **k):
        h = request.headers.get("Authorization", "")
        nom = store.verifier_jeton(h[7:]) if h.startswith("Bearer ") else None
        if not nom:
            return erreur("Authentification requise.", 401)
        g.admin = nom
        return f(*a, **k)
    return wrap


_echecs = {}          # (ip, nom) -> (nombre d'échecs, instant du dernier)
MAX_ECHECS, BLOCAGE = 5, 300


@app.post("/login")
def login():
    d = request.get_json(silent=True) or {}
    nom, mdp = str(d.get("nom", "")).strip().lower(), str(d.get("mot_de_passe", ""))
    cle = (request.remote_addr, nom)
    n, t = _echecs.get(cle, (0, 0))
    if n >= MAX_ECHECS and time.time() - t < BLOCAGE:
        return erreur("Trop de tentatives. Réessayez dans quelques minutes.", 429)
    if not store.verifier_admin(nom, mdp):
        _echecs[cle] = (n + 1 if time.time() - t < BLOCAGE else 1, time.time())
        return erreur("Identifiant ou mot de passe incorrect.", 401)
    _echecs.pop(cle, None)
    return jsonify({"jeton": store.creer_jeton(nom), "nom": nom})


@app.get("/moi")
@admin_requis
def moi():
    return jsonify({"nom": g.admin})


# ---------- documents ----------
def _slug(texte: str) -> str:
    s = unicodedata.normalize("NFD", texte.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60] or "document"


def _lire_texte(nom_ext: str, brut: bytes) -> str:
    if nom_ext in (".txt", ".md"):
        for enc in ("utf-8-sig", "cp1252"):
            try:
                return brut.decode(enc)
            except UnicodeDecodeError:
                continue
        return brut.decode("latin-1")
    if nom_ext == ".pdf":
        from pypdf import PdfReader
        pages = []
        for n, p in enumerate(PdfReader(io.BytesIO(brut)).pages, 1):
            t = (p.extract_text() or "").strip()
            if t:
                pages.append(f"[Page {n}]\n{t}")
        return "\n\n".join(pages)
    if nom_ext == ".docx":
        from docx import Document
        d = Document(io.BytesIO(brut))
        blocs = [p.text.strip() for p in d.paragraphs if p.text.strip()]
        for tab in d.tables:
            for lig in tab.rows:
                cellules = [c.text.strip() for c in lig.cells if c.text.strip()]
                if cellules:
                    blocs.append(" ; ".join(cellules))
        return "\n\n".join(blocs)
    raise ValueError("format non pris en charge")


def _origine(rel: Path) -> str:
    return {"uploads": "téléversé", "web": "site web", "db": "base de données", "api": "API"}.get(rel.parts[0], "dossier docs") \
        if len(rel.parts) > 1 else "dossier docs"


@app.get("/documents")
@admin_requis
def documents():
    out = []
    restreints = store.groupes_documents()
    for f in sorted(DOCS.rglob("*")):
        if f.suffix.lower() in (".txt", ".md") and f.is_file():
            rel = f.relative_to(DOCS)
            st = f.stat()
            out.append({"nom": rel.as_posix(), "origine": _origine(rel), "taille": st.st_size,
                        "modifie": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
                        "supprimable": rel.parts[0] == "uploads",
                        "groupes": restreints.get(rel.as_posix(), [])})
    return jsonify(out)


@app.post("/documents")
@admin_requis
def televerser():
    f = request.files.get("fichier")
    if not f or not f.filename:
        return erreur("Aucun fichier reçu.")
    original = Path(f.filename.replace("\\", "/")).name
    ext = Path(original).suffix.lower()
    if ext not in EXTENSIONS:
        return erreur("Format refusé. Acceptés : .txt, .md, .pdf, .docx.", 415)
    brut = f.read(MAX_OCTETS + 1)
    if len(brut) > MAX_OCTETS:
        return erreur("Fichier trop volumineux (10 Mo maximum).", 413)
    try:
        texte = _lire_texte(ext, brut)
    except Exception:
        return erreur("Fichier illisible ou corrompu.", 422)
    texte = texte.replace("\x00", "").strip()
    if len(texte.split()) < MIN_MOTS:
        return erreur("Aucun texte exploitable dans ce fichier (PDF scanné ? Il faudrait un OCR).", 422)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    cible = UPLOADS / f"{_slug(Path(original).stem)}.txt"
    existait = cible.exists()
    etiquette = re.sub(r"\s+", " ", original).strip()
    cible.write_text(f"SOURCE: {etiquette}\n\n{texte}\n", encoding="utf-8")
    return jsonify({"nom": cible.relative_to(DOCS).as_posix(), "remplace": existait,
                    "mots": len(texte.split())}), 201


@app.delete("/documents/<path:nom>")
@admin_requis
def supprimer_document(nom):
    cible = (DOCS / nom).resolve()
    if UPLOADS.resolve() not in cible.parents or cible.suffix != ".txt":
        return erreur("Seuls les documents téléversés peuvent être supprimés ici.", 403)
    if not cible.is_file():
        return erreur("Document introuvable.", 404)
    cible.unlink()
    store.oublier_document(cible.relative_to(DOCS.resolve()).as_posix())
    return jsonify({"ok": True})


@app.put("/documents/<path:nom>/groupes")
@admin_requis
def groupes_du_document(nom):
    """Corps : {"groupes": [ids]} ; liste vide = document public."""
    cible = (DOCS / nom).resolve()
    if DOCS.resolve() not in cible.parents or cible.suffix.lower() not in (".txt", ".md") or not cible.is_file():
        return erreur("Document introuvable.", 404)
    d = request.get_json(silent=True) or {}
    if not isinstance(d.get("groupes"), list):
        return erreur("« groupes » doit être une liste.")
    try:
        ids = store.definir_groupes_document(cible.relative_to(DOCS.resolve()).as_posix(), d["groupes"])
    except (ValueError, TypeError):
        return erreur("Groupe inconnu.")
    return jsonify({"groupes": ids})


# ---------- groupes et utilisateurs du chat ----------
@app.get("/groupes")
@admin_requis
def groupes():
    return jsonify(store.lister_groupes())


@app.post("/groupes")
@admin_requis
def creer_groupe():
    try:
        return jsonify({"id": store.enregistrer_groupe(request.get_json(silent=True) or {})}), 201
    except ValueError as e:
        return erreur(str(e))


@app.put("/groupes/<int:id_>")
@admin_requis
def modifier_groupe(id_):
    try:
        store.enregistrer_groupe(request.get_json(silent=True) or {}, id_)
    except LookupError as e:
        return erreur(str(e), 404)
    except ValueError as e:
        return erreur(str(e))
    return jsonify({"ok": True})


@app.delete("/groupes/<int:id_>")
@admin_requis
def supprimer_groupe(id_):
    if not store.supprimer_groupe(id_):
        return erreur("Groupe introuvable.", 404)
    return jsonify({"ok": True})


@app.get("/utilisateurs")
@admin_requis
def utilisateurs():
    return jsonify(store.lister_utilisateurs())


@app.post("/utilisateurs")
@admin_requis
def creer_utilisateur():
    d = request.get_json(silent=True) or {}
    try:
        store.creer_utilisateur(d.get("nom"), str(d.get("mot_de_passe") or ""), d.get("groupes") or [])
    except (ValueError, TypeError) as e:
        return erreur(str(e))
    return jsonify({"ok": True}), 201


@app.put("/utilisateurs/<nom>")
@admin_requis
def modifier_utilisateur(nom):
    d = request.get_json(silent=True) or {}
    try:
        store.modifier_utilisateur(nom, d.get("mot_de_passe") or None, d.get("actif"), d.get("groupes"))
    except LookupError as e:
        return erreur(str(e), 404)
    except (ValueError, TypeError) as e:
        return erreur(str(e))
    return jsonify({"ok": True})


@app.delete("/utilisateurs/<nom>")
@admin_requis
def supprimer_utilisateur(nom):
    if not store.supprimer_utilisateur(nom):
        return erreur("Utilisateur introuvable.", 404)
    return jsonify({"ok": True})


# ---------- connexion des utilisateurs du chat (publique : pas de jeton admin) ----------
@app.post("/chat/connexion")
def chat_connexion():
    d = request.get_json(silent=True) or {}
    nom = str(d.get("nom", "")).strip().lower()
    cle = ("chat", request.remote_addr, nom)
    n, t = _echecs.get(cle, (0, 0))
    if n >= MAX_ECHECS and time.time() - t < BLOCAGE:
        return erreur("Trop de tentatives. Réessayez dans quelques minutes.", 429)
    r = store.ouvrir_session_chat(nom, str(d.get("mot_de_passe", "")))
    if not r:
        _echecs[cle] = (n + 1 if time.time() - t < BLOCAGE else 1, time.time())
        return erreur("Identifiant ou mot de passe incorrect.", 401)
    _echecs.pop(cle, None)
    return jsonify({"sender_id": r[0], "nom": r[1], "groupes": r[2]})


@app.post("/chat/session")
def chat_session():
    """Le widget vérifie au chargement que sa session est toujours valide."""
    d = request.get_json(silent=True) or {}
    r = store.session_chat(str(d.get("sender_id", "")))
    if not r:
        return erreur("Session expirée.", 401)
    return jsonify({"nom": r[0], "groupes": r[1]})


@app.post("/chat/nouvelle")
def chat_nouvelle():
    """« Nouvelle conversation » d'un utilisateur connecté : il garde ses droits, repart d'un suivi vierge."""
    d = request.get_json(silent=True) or {}
    r = store.renouveler_session_chat(str(d.get("sender_id", "")))
    if not r:
        return erreur("Session expirée.", 401)
    return jsonify({"sender_id": r[0], "nom": r[1], "groupes": r[2]})


@app.post("/chat/deconnexion")
def chat_deconnexion():
    d = request.get_json(silent=True) or {}
    store.fermer_session_chat(str(d.get("sender_id", "")))
    return jsonify({"ok": True})


# ---------- connexions aux bases ----------
def _url_depuis_formulaire(d: dict, mdp: str):
    dial = d.get("dialecte")
    if dial == "sqlite":
        return f"sqlite:///{d.get('base', '')}"
    if dial not in store.DRIVERS:
        raise ValueError("dialecte inconnu")
    port = int(d["port"]) if d.get("port") not in (None, "") else None
    return URL.create(store.DRIVERS[dial], username=d.get("utilisateur") or None, password=mdp or None,
                      host=d.get("hote") or None, port=port, database=d.get("base") or None
                      ).render_as_string(hide_password=False)


def _moteur_test(url: str):
    if url.startswith("sqlite"):
        args = {}
    elif url.startswith("mssql"):
        args = {"login_timeout": 5}
    else:
        args = {"connect_timeout": 5}
    return create_engine(url, connect_args=args, pool_pre_ping=False)


def _masquer(msg: str, *secrets_) -> str:
    for s in secrets_:
        if s:
            msg = msg.replace(s, "***")
    return msg[:300]


@app.get("/sources")
@admin_requis
def sources():
    return jsonify(store.lister_sources())


def _valider_et_enregistrer(d: dict, id_=None):
    try:
        return jsonify({"id": store.enregistrer_source(d, id_)}), (200 if id_ else 201)
    except ValueError as e:
        return erreur(str(e))


@app.post("/sources")
@admin_requis
def creer_source():
    return _valider_et_enregistrer(request.get_json(silent=True) or {})


@app.put("/sources/<int:id_>")
@admin_requis
def modifier_source(id_):
    if not store.obtenir_source(id_):
        return erreur("Connexion introuvable.", 404)
    return _valider_et_enregistrer(request.get_json(silent=True) or {}, id_)


@app.delete("/sources/<int:id_>")
@admin_requis
def supprimer_source(id_):
    store.supprimer_source(id_)
    return jsonify({"ok": True})


@app.post("/sources/tester")
@admin_requis
def tester():
    """Teste les valeurs du formulaire SANS les enregistrer. Si 'id' est fourni et le mot de passe vide,
    on utilise le mot de passe déjà enregistré."""
    d = request.get_json(silent=True) or {}
    mdp = d.get("mot_de_passe") or ""
    if not mdp and d.get("id"):
        try:
            u = store.url_source(int(d["id"]))
            mdp = (make_url_mdp(u) if u else "") or ""
        except Exception:
            mdp = ""
    try:
        url = _url_depuis_formulaire(d, mdp)
        with _moteur_test(url).connect() as c:
            c.execute(text("SELECT 1"))
            tables = sorted(inspect(c).get_table_names())
        return jsonify({"ok": True, "tables": tables[:200]})
    except Exception as e:
        return jsonify({"ok": False, "message": _masquer(f"{e.__class__.__name__}: {str(e).splitlines()[0]}", mdp)})


def make_url_mdp(url: str):
    from sqlalchemy.engine import make_url
    return make_url(url).password


@app.get("/sources/<int:id_>/tables")
@admin_requis
def tables_source(id_):
    try:
        url = store.url_source(id_)
        if not url:
            return erreur("Connexion introuvable.", 404)
        with _moteur_test(url).connect() as c:
            insp = inspect(c)
            noms = sorted(insp.get_table_names())[:200]
            return jsonify({t: [col["name"] for col in insp.get_columns(t)] for t in noms})
    except Exception as e:
        return erreur(f"Connexion impossible : {_masquer(str(e).splitlines()[0])}", 502)


@app.put("/sources/<int:id_>/exports")
@admin_requis
def definir_exports(id_):
    if not store.obtenir_source(id_):
        return erreur("Connexion introuvable.", 404)
    exports = (request.get_json(silent=True) or {}).get("exports")
    if not isinstance(exports, list) or len(exports) > 50:
        return erreur("Liste d'exports invalide.")
    propres = []
    try:
        for e in exports:
            p = {"table": export_db.verifier_ident(e.get("table"), "table"),
                 "id": export_db.verifier_ident(e.get("id"), "colonne id"),
                 "colonnes": [export_db.verifier_ident(c, "colonne") for c in e.get("colonnes", [])],
                 "max_lignes": max(1, min(int(e.get("max_lignes") or 500), 5000))}
            if not p["colonnes"]:
                return erreur(f"Table {p['table']} : choisissez au moins une colonne de texte.")
            if e.get("titre"):
                p["titre"] = export_db.verifier_ident(e["titre"], "colonne titre")
            f = e.get("filtre") or {}
            if f.get("colonne"):
                p["filtre"] = {"colonne": export_db.verifier_ident(f["colonne"], "colonne du filtre"),
                               "valeur": str(f.get("valeur", ""))}
            if e.get("libelle"):
                p["libelle"] = str(e["libelle"])[:80]
            propres.append(p)
    except (ValueError, TypeError) as ex:
        return erreur(str(ex))
    store.enregistrer_exports(id_, propres)
    return jsonify({"ok": True})


@app.post("/exports/lancer")
@admin_requis
def lancer_exports():
    id_ = (request.get_json(silent=True) or {}).get("source")
    bilan = export_db.exporter_depuis_admin(DOCS / "db", id_source=int(id_) if id_ else None)
    return jsonify(bilan)


# ---------- collecte du site du PAD (déclenchée à la main) ----------
SCRAPER = RACINE / "scrape_site.py"
JOURNAL = RACINE / "scrape.log"
SITE_DEFAUT = {"url": "https://www.portdakar.sn", "max_pages": 150, "insecure": False}
_job = {"proc": None, "debut": None, "fin": None, "code": None, "url": None}
_verrou_job = threading.Lock()


def _url_autorisee(url: str) -> bool:
    """http(s) uniquement, et pas d'adresse interne (évite d'utiliser le serveur pour sonder le réseau local)."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    if os.getenv("PAD_SCRAPE_ALLOW_PRIVATE") == "1":
        return True
    try:
        for info in socket.getaddrinfo(p.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
    except (socket.gaierror, ValueError):
        return False
    return True


def _etat():
    proc = _job["proc"]
    if proc is not None and proc.poll() is not None and _job["fin"] is None:
        _job["fin"], _job["code"] = time.time(), proc.returncode
    lignes = []
    if JOURNAL.exists():
        lignes = JOURNAL.read_text(encoding="utf-8", errors="replace").splitlines()
    pages = 0
    for l in lignes:
        m = re.match(r"\s*\[(\d+)\]", l)
        if m:
            pages = int(m.group(1))
    return {"en_cours": proc is not None and proc.poll() is None, "debut": _job["debut"], "fin": _job["fin"],
            "code": _job["code"], "url": _job["url"], "pages": pages, "journal": [l.strip() for l in lignes[-12:]]}


@app.get("/site/reglages")
@admin_requis
def site_reglages():
    return jsonify({k: store.lire_reglage("site_" + k, v) for k, v in SITE_DEFAUT.items()})


@app.get("/site/etat")
@admin_requis
def site_etat():
    return jsonify(_etat())


@app.post("/site/lancer")
@admin_requis
def site_lancer():
    d = request.get_json(silent=True) or {}
    url = str(d.get("url", "")).strip().rstrip("/")
    if not _url_autorisee(url):
        return erreur("Adresse refusée : utilisez l'adresse publique d'un site (http ou https).")
    try:
        maxi = max(1, min(int(d.get("max_pages", 150)), 500))
    except (TypeError, ValueError):
        return erreur("Nombre de pages invalide.")
    insecure = bool(d.get("insecure"))
    with _verrou_job:
        if _etat()["en_cours"]:
            return erreur("Une collecte est déjà en cours.", 409)
        cmd = [sys.executable, str(SCRAPER), url, "--max-pages", str(maxi), "--sortie", str(DOCS / "web"),
               "--delai", os.getenv("PAD_SCRAPE_DELAY", "1.5")] + (["--insecure"] if insecure else [])
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        journal = open(JOURNAL, "w", encoding="utf-8")
        _job.update(proc=subprocess.Popen(cmd, cwd=str(RACINE), stdout=journal, stderr=subprocess.STDOUT, env=env),
                    debut=time.time(), fin=None, code=None, url=url)
        for k, v in (("url", url), ("max_pages", maxi), ("insecure", insecure)):
            store.ecrire_reglage("site_" + k, v)
    return jsonify(_etat()), 202


@app.post("/site/arreter")
@admin_requis
def site_arreter():
    with _verrou_job:
        proc = _job["proc"]
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    return jsonify(_etat())


# ---------- sources API ----------
def _config_api(d: dict) -> dict:
    """Valide et nettoie la configuration d'import envoyée par l'interface."""
    c = d.get("config") or {}
    pag = c.get("pagination") or {}
    typ = pag.get("type", "aucune")
    if typ not in ("aucune", "page", "suivant"):
        raise ValueError("pagination invalide")
    champ = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
    out = {"chemin_liste": str(c.get("chemin_liste", "")).strip(), "pagination": {"type": typ},
           "max_elements": max(1, min(int(c.get("max_elements") or 500), 5000)),
           "libelle": str(c.get("libelle", ""))[:80]}
    if out["chemin_liste"] and not champ.match(out["chemin_liste"]):
        raise ValueError("chemin de la liste invalide")
    if typ == "page":
        out["pagination"].update(param=str(pag.get("param") or "page"), debut=int(pag.get("debut", 1)))
        if not champ.match(out["pagination"]["param"]):
            raise ValueError("nom du paramètre de page invalide")
    if typ == "suivant":
        out["pagination"]["champ_suivant"] = str(pag.get("champ_suivant") or "next")
        if not champ.match(out["pagination"]["champ_suivant"]):
            raise ValueError("champ « page suivante » invalide")
    for k in ("champ_id", "champ_titre"):
        v = str(c.get(k) or "").strip()
        if v and not champ.match(v):
            raise ValueError(f"{k} invalide")
        out[k] = v
    out["champs_texte"] = [str(x) for x in (c.get("champs_texte") or [])][:30]
    if not all(champ.match(x) for x in out["champs_texte"]):
        raise ValueError("nom de champ de texte invalide")
    return out


def _preparer_api(d: dict):
    if not import_api.url_autorisee(str(d.get("url", "")).strip()):
        raise ValueError("adresse refusée : utilisez l'adresse publique d'une API (http ou https)")
    d = dict(d, config=_config_api(d))
    import_api.verifier_entete(str(d.get("entete", "")).strip(), str(d.get("cle") or ""))
    return d


@app.get("/apis")
@admin_requis
def apis():
    return jsonify(store.lister_apis())


@app.post("/apis")
@admin_requis
def creer_api():
    try:
        return jsonify({"id": store.enregistrer_api(_preparer_api(request.get_json(silent=True) or {}))}), 201
    except (ValueError, TypeError) as e:
        return erreur(str(e))


def _meme_hote(a: str, b: str) -> bool:
    return (urlparse(a).hostname or "").lower() == (urlparse(b).hostname or "").lower()


@app.put("/apis/<int:id_>")
@admin_requis
def modifier_api(id_):
    ancienne = store.obtenir_api(id_)
    if not ancienne:
        return erreur("API introuvable.", 404)
    d = request.get_json(silent=True) or {}
    if ancienne["a_une_cle"] and not d.get("cle") and not _meme_hote(d.get("url", ""), ancienne["url"]):
        # la clé enregistrée ne doit jamais partir vers un autre site : il faut la ressaisir
        return erreur("Le site a changé : saisissez à nouveau la clé d'API.")
    try:
        return jsonify({"id": store.enregistrer_api(_preparer_api(request.get_json(silent=True) or {}), id_)})
    except (ValueError, TypeError) as e:
        return erreur(str(e))


@app.delete("/apis/<int:id_>")
@admin_requis
def supprimer_api(id_):
    store.supprimer_api(id_)
    return jsonify({"ok": True})


@app.post("/apis/tester")
@admin_requis
def tester_api():
    """Appelle l'API avec les valeurs du formulaire (sans enregistrer) et montre les champs trouvés."""
    d = request.get_json(silent=True) or {}
    cle = d.get("cle") or ""
    entete = str(d.get("entete", "")).strip()
    try:
        ancienne = store.obtenir_api(int(d["id"])) if d.get("id") else None
        if not cle and ancienne:
            if not _meme_hote(d.get("url", ""), ancienne["url"]):
                raise ValueError("le site a changé : saisissez à nouveau la clé d'API pour tester")
            entete_s, cle = store.cle_api(ancienne["id"])
            entete = entete or entete_s
        d = _preparer_api(d)
        elements = import_api.recuperer(dict(d["config"], url=d["url"]), entete, cle, limite_pages=1)
        return jsonify({"ok": True, "nombre": len(elements), "champs": import_api.apercu(elements)})
    except Exception as e:
        msg = str(e).splitlines()[0] if str(e) else e.__class__.__name__
        return jsonify({"ok": False, "message": _masquer(msg, cle)})


@app.post("/apis/<int:id_>/importer")
@admin_requis
def importer_api(id_):
    src = store.obtenir_api(id_)
    if not src:
        return erreur("API introuvable.", 404)
    bilan = import_api.importer_depuis_admin(DOCS / "api", id_source=id_)
    return jsonify(bilan)


if __name__ == "__main__":
    app.run(host=os.getenv("PAD_ADMIN_HOST", "127.0.0.1"), port=int(os.getenv("PAD_ADMIN_PORT", "8010")))
