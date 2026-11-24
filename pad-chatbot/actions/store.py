"""Magasin de configuration de l'administration : comptes admin et connexions aux bases de données.

Un seul fichier SQLite (admin.db, à la racine de pad-chatbot, ou PAD_ADMIN_DB). On y trouve :
  * les comptes admin : mot de passe jamais stocké, seulement une empreinte scrypt salée ;
  * les connexions aux bases : mot de passe CHIFFRÉ (Fernet) avec la clé du fichier .admin_secret
    (créée au premier usage, ou variable PAD_SECRET). Sans cette clé, admin.db est inutilisable :
    sauvegardez-les séparément et ne mettez ni l'un ni l'autre dans Git.
Les jetons de connexion sont signés (HMAC) et expirent au bout de 8 h.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

RACINE = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("PAD_ADMIN_DB") or RACINE / "admin.db")
SECRET_PATH = Path(os.getenv("PAD_SECRET_FILE") or RACINE / ".admin_secret")
DUREE_JETON = 8 * 3600
DRIVERS = {"postgresql": "postgresql+psycopg2", "mysql": "mysql+pymysql", "mssql": "mssql+pymssql", "sqlite": "sqlite"}


# ---------- secret ----------
def _secret() -> bytes:
    brut = os.getenv("PAD_SECRET")
    if brut:
        return hashlib.sha256(brut.encode()).digest()
    if not SECRET_PATH.exists():
        SECRET_PATH.write_text(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
        try:
            SECRET_PATH.chmod(0o600)
        except OSError:
            pass
    return hashlib.sha256(SECRET_PATH.read_text().strip().encode()).digest()


def _sous_cle(etiquette: bytes) -> bytes:
    return hmac.new(_secret(), etiquette, hashlib.sha256).digest()


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_sous_cle(b"chiffrement")))


def chiffrer(clair: str) -> str:
    return _fernet().encrypt(clair.encode()).decode()


def dechiffrer(chiffre: str) -> str:
    try:
        return _fernet().decrypt(chiffre.encode()).decode()
    except InvalidToken:
        raise ValueError("clé secrète incorrecte : impossible de déchiffrer le mot de passe")


# ---------- base ----------
def _co() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS admins (nom TEXT PRIMARY KEY, empreinte TEXT NOT NULL, cree_le TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reglages (cle TEXT PRIMARY KEY, valeur TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS api_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT UNIQUE NOT NULL, url TEXT NOT NULL,
            entete TEXT NOT NULL DEFAULT '', valeur_chiffree TEXT NOT NULL DEFAULT '',
            config TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT UNIQUE NOT NULL, dialecte TEXT NOT NULL,
            hote TEXT, port INTEGER, base TEXT NOT NULL, utilisateur TEXT, mdp_chiffre TEXT,
            role TEXT NOT NULL DEFAULT 'export', exports TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS utilisateurs (
            nom TEXT PRIMARY KEY, empreinte TEXT NOT NULL, actif INTEGER NOT NULL DEFAULT 1, cree_le TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS groupes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT UNIQUE NOT NULL, description TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS membres (
            utilisateur TEXT NOT NULL, groupe_id INTEGER NOT NULL, PRIMARY KEY (utilisateur, groupe_id));
        CREATE TABLE IF NOT EXISTS doc_groupes (
            doc TEXT NOT NULL, groupe_id INTEGER NOT NULL, PRIMARY KEY (doc, groupe_id));
        CREATE TABLE IF NOT EXISTS sessions_chat (
            sender_id TEXT PRIMARY KEY, utilisateur TEXT NOT NULL, expire INTEGER NOT NULL);
    """)
    return c


# ---------- comptes admin ----------
_NOM = re.compile(r"^[a-z0-9_.-]{3,40}$")
_N, _R, _P = 2 ** 14, 8, 1


def _empreinte(mdp: str, sel: bytes) -> str:
    h = hashlib.scrypt(mdp.encode(), salt=sel, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(sel).decode()}${base64.b64encode(h).decode()}"


def creer_admin(nom: str, mdp: str) -> None:
    if not _NOM.match(nom):
        raise ValueError("nom : 3 à 40 caractères parmi a-z, 0-9, _ . -")
    if len(mdp) < 10:
        raise ValueError("mot de passe : 10 caractères minimum")
    with _co() as c:
        c.execute("INSERT OR REPLACE INTO admins VALUES (?, ?, ?)",
                  (nom, _empreinte(mdp, secrets.token_bytes(16)), time.strftime("%Y-%m-%dT%H:%M:%S")))


_FAUX = _empreinte("x", b"0" * 16)


def verifier_admin(nom: str, mdp: str) -> bool:
    with _co() as c:
        r = c.execute("SELECT empreinte FROM admins WHERE nom = ?", (nom,)).fetchone()
    emp = r["empreinte"] if r else _FAUX            # même durée de calcul si le compte n'existe pas
    try:
        _, n, rr, p, sel, h = emp.split("$")
        calc = hashlib.scrypt(mdp.encode(), salt=base64.b64decode(sel), n=int(n), r=int(rr), p=int(p), dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(h)) and r is not None
    except Exception:
        return False


def nb_admins() -> int:
    with _co() as c:
        return c.execute("SELECT COUNT(*) FROM admins").fetchone()[0]


# ---------- jetons ----------
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def creer_jeton(nom: str, duree: int = DUREE_JETON) -> str:
    corps = _b64(json.dumps({"u": nom, "exp": int(time.time()) + duree}).encode())
    sig = _b64(hmac.new(_sous_cle(b"jeton"), corps.encode(), hashlib.sha256).digest())
    return f"{corps}.{sig}"


def verifier_jeton(jeton: str):
    """Renvoie le nom de l'admin, ou None si le jeton est faux, falsifié ou expiré."""
    try:
        corps, sig = jeton.split(".")
        attendu = _b64(hmac.new(_sous_cle(b"jeton"), corps.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, attendu):
            return None
        d = json.loads(_unb64(corps))
        return d["u"] if d["exp"] > time.time() else None
    except Exception:
        return None


# ---------- réglages simples ----------
def lire_reglage(cle: str, defaut=None):
    with _co() as c:
        r = c.execute("SELECT valeur FROM reglages WHERE cle = ?", (cle,)).fetchone()
    return json.loads(r["valeur"]) if r else defaut


def ecrire_reglage(cle: str, valeur) -> None:
    with _co() as c:
        c.execute("INSERT OR REPLACE INTO reglages VALUES (?, ?)", (cle, json.dumps(valeur)))


# ---------- connexions aux bases ----------
def _public(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["a_un_mdp"] = bool(d.pop("mdp_chiffre"))        # le mot de passe n'est jamais renvoyé
    d["exports"] = json.loads(d["exports"])
    return d


def lister_sources() -> list:
    with _co() as c:
        return [_public(r) for r in c.execute("SELECT * FROM sources ORDER BY nom")]


def obtenir_source(id_: int):
    with _co() as c:
        r = c.execute("SELECT * FROM sources WHERE id = ?", (id_,)).fetchone()
    return _public(r) if r else None


def enregistrer_source(d: dict, id_: int = None) -> int:
    """Crée (id_ None) ou modifie. Un mot de passe vide à la modification conserve l'ancien."""
    dial = d.get("dialecte")
    if dial not in DRIVERS:
        raise ValueError("dialecte : postgresql, mysql ou sqlite")
    nom = str(d.get("nom", "")).strip()
    if not re.match(r"^[A-Za-z0-9_-]{2,40}$", nom):
        raise ValueError("nom : 2 à 40 caractères (lettres, chiffres, _ -)")
    base = str(d.get("base", "")).strip()
    if not base:
        raise ValueError("nom de la base (ou chemin du fichier SQLite) manquant")
    if d.get("role", "export") not in ("export", "principale"):
        raise ValueError("rôle invalide")
    port = int(d["port"]) if d.get("port") not in (None, "") else None
    with _co() as c:
        mdp = d.get("mot_de_passe") or ""
        ancien = c.execute("SELECT mdp_chiffre, exports FROM sources WHERE id = ?", (id_,)).fetchone() if id_ else None
        chiffre = chiffrer(mdp) if mdp else (ancien["mdp_chiffre"] if ancien else "")
        role = d.get("role", "export")
        if role == "principale":
            c.execute("UPDATE sources SET role = 'export' WHERE role = 'principale' AND id IS NOT ?", (id_,))
        vals = (nom, dial, (d.get("hote") or "").strip() or None, port, base,
                (d.get("utilisateur") or "").strip() or None, chiffre, role)
        try:
            if id_:
                c.execute("UPDATE sources SET nom=?, dialecte=?, hote=?, port=?, base=?, utilisateur=?, "
                          "mdp_chiffre=?, role=? WHERE id=?", vals + (id_,))
                return id_
            cur = c.execute("INSERT INTO sources (nom, dialecte, hote, port, base, utilisateur, mdp_chiffre, role) "
                            "VALUES (?,?,?,?,?,?,?,?)", vals)
            return cur.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError("une connexion porte déjà ce nom")


def enregistrer_exports(id_: int, exports: list) -> None:
    with _co() as c:
        c.execute("UPDATE sources SET exports = ? WHERE id = ?", (json.dumps(exports), id_))


def supprimer_source(id_: int) -> None:
    with _co() as c:
        c.execute("DELETE FROM sources WHERE id = ?", (id_,))


def url_source(id_: int):
    """URL SQLAlchemy complète (avec mot de passe déchiffré) pour se connecter, ou None."""
    from sqlalchemy.engine import URL
    with _co() as c:
        r = c.execute("SELECT * FROM sources WHERE id = ?", (id_,)).fetchone()
    if not r:
        return None
    mdp = dechiffrer(r["mdp_chiffre"]) if r["mdp_chiffre"] else None
    if r["dialecte"] == "sqlite":
        return f"sqlite:///{r['base']}"
    u = URL.create(DRIVERS[r["dialecte"]], username=r["utilisateur"], password=mdp,
                   host=r["hote"], port=r["port"], database=r["base"])
    return u.render_as_string(hide_password=False)


def url_principale():
    """URL de la base principale choisie dans l'interface, ou None."""
    if not DB_PATH.exists():
        return None
    with _co() as c:
        r = c.execute("SELECT id FROM sources WHERE role = 'principale'").fetchone()
    return url_source(r["id"]) if r else None


# ---------- sources API (import de textes depuis une API JSON) ----------
def _api_public(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["a_une_cle"] = bool(d.pop("valeur_chiffree"))      # la clé n'est jamais renvoyée
    d["config"] = json.loads(d["config"])
    return d


def lister_apis() -> list:
    with _co() as c:
        return [_api_public(r) for r in c.execute("SELECT * FROM api_sources ORDER BY nom")]


def obtenir_api(id_: int):
    with _co() as c:
        r = c.execute("SELECT * FROM api_sources WHERE id = ?", (id_,)).fetchone()
    return _api_public(r) if r else None


def cle_api(id_: int):
    """(nom de l'en-tête, valeur déchiffrée) à envoyer à l'API ; ('', '') si pas de clé."""
    with _co() as c:
        r = c.execute("SELECT entete, valeur_chiffree FROM api_sources WHERE id = ?", (id_,)).fetchone()
    if not r or not r["valeur_chiffree"]:
        return "", ""
    return r["entete"], dechiffrer(r["valeur_chiffree"])


def enregistrer_api(d: dict, id_: int = None) -> int:
    """Crée ou modifie. Une valeur de clé vide à la modification conserve l'ancienne."""
    nom = str(d.get("nom", "")).strip()
    if not re.match(r"^[A-Za-z0-9_-]{2,40}$", nom):
        raise ValueError("nom : 2 à 40 caractères (lettres, chiffres, _ -)")
    url = str(d.get("url", "")).strip()
    if not re.match(r"^https?://", url):
        raise ValueError("l'adresse doit commencer par http:// ou https://")
    entete = str(d.get("entete", "")).strip()
    if entete and not re.match(r"^[A-Za-z0-9-]{1,40}$", entete):
        raise ValueError("nom d'en-tête invalide")
    cle = str(d.get("cle") or "")
    if re.search(r"[\r\n]", cle):
        raise ValueError("clé invalide")
    config = dict(d.get("config") or {})
    with _co() as c:
        ancien = c.execute("SELECT valeur_chiffree FROM api_sources WHERE id = ?", (id_,)).fetchone() if id_ else None
        chiffre = chiffrer(cle) if cle else (ancien["valeur_chiffree"] if ancien else "")
        try:
            if id_:
                c.execute("UPDATE api_sources SET nom=?, url=?, entete=?, valeur_chiffree=?, config=? WHERE id=?",
                          (nom, url, entete, chiffre, json.dumps(config), id_))
                return id_
            return c.execute("INSERT INTO api_sources (nom, url, entete, valeur_chiffree, config) VALUES (?,?,?,?,?)",
                             (nom, url, entete, chiffre, json.dumps(config))).lastrowid
        except sqlite3.IntegrityError:
            raise ValueError("une API porte déjà ce nom")


def supprimer_api(id_: int) -> None:
    with _co() as c:
        c.execute("DELETE FROM api_sources WHERE id = ?", (id_,))


# ====================================================================================
# Utilisateurs du chat, groupes, visibilité des documents
# Règle : un document SANS groupe est public. Un document AVEC groupe(s) n'est utilisé que pour
# les utilisateurs connectés membres d'au moins un de ces groupes.
# ====================================================================================
DUREE_SESSION_CHAT = int(os.getenv("PAD_SESSION_CHAT_H", "12")) * 3600
_NOM_UTIL = re.compile(r"^[a-z0-9_.@-]{3,60}$")


def _verifier_empreinte(emp: str, mdp: str) -> bool:
    try:
        _, n, rr, p, sel, h = emp.split("$")
        calc = hashlib.scrypt(mdp.encode(), salt=base64.b64decode(sel), n=int(n), r=int(rr), p=int(p), dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except Exception:
        return False


def _verifier_mdp_utilisateur(mdp: str) -> None:
    if len(mdp) < 8:
        raise ValueError("mot de passe : 8 caractères minimum")


# ---------- groupes ----------
def _nom_groupe(nom) -> str:
    nom = str(nom or "").strip()
    if not 2 <= len(nom) <= 60 or re.search(r"[\r\n<>]", nom):
        raise ValueError("nom de groupe : 2 à 60 caractères")
    return nom


def lister_groupes() -> list:
    with _co() as c:
        out = []
        for r in c.execute("SELECT * FROM groupes ORDER BY nom"):
            d = dict(r)
            d["nb_membres"] = c.execute("SELECT COUNT(*) FROM membres WHERE groupe_id = ?", (r["id"],)).fetchone()[0]
            d["nb_documents"] = c.execute("SELECT COUNT(*) FROM doc_groupes WHERE groupe_id = ?", (r["id"],)).fetchone()[0]
            out.append(d)
    return out


def enregistrer_groupe(d: dict, id_: int = None) -> int:
    nom = _nom_groupe(d.get("nom"))
    desc = str(d.get("description") or "")[:300]
    with _co() as c:
        try:
            if id_:
                if not c.execute("SELECT 1 FROM groupes WHERE id = ?", (id_,)).fetchone():
                    raise LookupError("groupe introuvable")
                c.execute("UPDATE groupes SET nom = ?, description = ? WHERE id = ?", (nom, desc, id_))
                return id_
            return c.execute("INSERT INTO groupes (nom, description) VALUES (?, ?)", (nom, desc)).lastrowid
        except sqlite3.IntegrityError:
            raise ValueError("un groupe porte déjà ce nom")


def supprimer_groupe(id_: int) -> bool:
    """Supprime le groupe. Attention : un document qui n'avait QUE ce groupe redevient public ;
    l'administration le signale avant (nb_documents)."""
    with _co() as c:
        c.execute("DELETE FROM membres WHERE groupe_id = ?", (id_,))
        c.execute("DELETE FROM doc_groupes WHERE groupe_id = ?", (id_,))
        return c.execute("DELETE FROM groupes WHERE id = ?", (id_,)).rowcount > 0


def _ids_groupes_valides(c, ids) -> list:
    ids = sorted({int(i) for i in (ids or [])})
    if ids:
        trouves = {r[0] for r in c.execute(
            f"SELECT id FROM groupes WHERE id IN ({','.join('?' * len(ids))})", ids)}
        if set(ids) - trouves:
            raise ValueError("groupe inconnu")
    return ids


# ---------- utilisateurs ----------
def _util_public(c, r) -> dict:
    return {"nom": r["nom"], "actif": bool(r["actif"]), "cree_le": r["cree_le"],
            "groupes": [x[0] for x in c.execute(
                "SELECT groupe_id FROM membres WHERE utilisateur = ? ORDER BY groupe_id", (r["nom"],))]}


def lister_utilisateurs() -> list:
    with _co() as c:
        return [_util_public(c, r) for r in c.execute("SELECT * FROM utilisateurs ORDER BY nom")]


def creer_utilisateur(nom: str, mdp: str, groupes=None) -> None:
    nom = str(nom or "").strip().lower()
    if not _NOM_UTIL.match(nom):
        raise ValueError("identifiant : 3 à 60 caractères parmi a-z, 0-9, _ . @ -")
    _verifier_mdp_utilisateur(mdp)
    with _co() as c:
        ids = _ids_groupes_valides(c, groupes)
        try:
            c.execute("INSERT INTO utilisateurs VALUES (?, ?, 1, ?)",
                      (nom, _empreinte(mdp, secrets.token_bytes(16)), time.strftime("%Y-%m-%dT%H:%M:%S")))
        except sqlite3.IntegrityError:
            raise ValueError("cet identifiant existe déjà")
        c.executemany("INSERT INTO membres VALUES (?, ?)", [(nom, i) for i in ids])


def modifier_utilisateur(nom: str, mdp: str = None, actif=None, groupes=None) -> None:
    """Champs absents (None) = inchangés. Désactiver ou changer le mot de passe coupe ses sessions."""
    with _co() as c:
        if not c.execute("SELECT 1 FROM utilisateurs WHERE nom = ?", (nom,)).fetchone():
            raise LookupError("utilisateur introuvable")
        ids = _ids_groupes_valides(c, groupes) if groupes is not None else None
        if mdp:
            _verifier_mdp_utilisateur(mdp)
            c.execute("UPDATE utilisateurs SET empreinte = ? WHERE nom = ?",
                      (_empreinte(mdp, secrets.token_bytes(16)), nom))
            c.execute("DELETE FROM sessions_chat WHERE utilisateur = ?", (nom,))
        if actif is not None:
            c.execute("UPDATE utilisateurs SET actif = ? WHERE nom = ?", (1 if actif else 0, nom))
            if not actif:
                c.execute("DELETE FROM sessions_chat WHERE utilisateur = ?", (nom,))
        if ids is not None:
            c.execute("DELETE FROM membres WHERE utilisateur = ?", (nom,))
            c.executemany("INSERT INTO membres VALUES (?, ?)", [(nom, i) for i in ids])


def supprimer_utilisateur(nom: str) -> bool:
    with _co() as c:
        c.execute("DELETE FROM membres WHERE utilisateur = ?", (nom,))
        c.execute("DELETE FROM sessions_chat WHERE utilisateur = ?", (nom,))
        return c.execute("DELETE FROM utilisateurs WHERE nom = ?", (nom,)).rowcount > 0


# ---------- documents ↔ groupes ----------
def groupes_documents() -> dict:
    """{chemin relatif du document: [ids de groupes]} pour les documents restreints."""
    out = {}
    with _co() as c:
        for r in c.execute("SELECT doc, groupe_id FROM doc_groupes ORDER BY doc, groupe_id"):
            out.setdefault(r["doc"], []).append(r["groupe_id"])
    return out


def definir_groupes_document(doc: str, groupes) -> list:
    """Liste vide = document public."""
    with _co() as c:
        ids = _ids_groupes_valides(c, groupes)
        c.execute("DELETE FROM doc_groupes WHERE doc = ?", (doc,))
        c.executemany("INSERT INTO doc_groupes VALUES (?, ?)", [(doc, i) for i in ids])
    return ids


def oublier_document(doc: str) -> None:
    with _co() as c:
        c.execute("DELETE FROM doc_groupes WHERE doc = ?", (doc,))


# ---------- connexion au chat ----------
_FAUX_U = _empreinte("x", b"1" * 16)


def ouvrir_session_chat(nom: str, mdp: str):
    """Vérifie l'utilisateur. Renvoie (sender_id, nom, [noms des groupes]) ou None.
    sender_id = identifiant de conversation aléatoire (256 bits) créé par le serveur : il sert de
    secret, jamais deviné ni choisi par le navigateur."""
    nom = str(nom or "").strip().lower()
    with _co() as c:
        r = c.execute("SELECT empreinte, actif FROM utilisateurs WHERE nom = ?", (nom,)).fetchone()
        ok = _verifier_empreinte(r["empreinte"] if r else _FAUX_U, mdp) and r is not None and bool(r["actif"])
        if not ok:
            return None
        sid = "u-" + secrets.token_hex(32)
        c.execute("DELETE FROM sessions_chat WHERE expire < ?", (int(time.time()),))
        c.execute("INSERT INTO sessions_chat VALUES (?, ?, ?)", (sid, nom, int(time.time()) + DUREE_SESSION_CHAT))
        noms = [x[0] for x in c.execute(
            "SELECT g.nom FROM groupes g JOIN membres m ON m.groupe_id = g.id WHERE m.utilisateur = ? ORDER BY g.nom", (nom,))]
    return sid, nom, noms


def session_chat(sender_id: str):
    """Renvoie (nom, [noms de groupes]) si la session est valide, sinon None."""
    if not sender_id or not str(sender_id).startswith("u-"):
        return None
    with _co() as c:
        r = c.execute("SELECT s.utilisateur FROM sessions_chat s JOIN utilisateurs u ON u.nom = s.utilisateur "
                      "WHERE s.sender_id = ? AND s.expire > ? AND u.actif = 1",
                      (sender_id, int(time.time()))).fetchone()
        if not r:
            return None
        noms = [x[0] for x in c.execute(
            "SELECT g.nom FROM groupes g JOIN membres m ON m.groupe_id = g.id WHERE m.utilisateur = ? ORDER BY g.nom",
            (r["utilisateur"],))]
    return r["utilisateur"], noms


def renouveler_session_chat(sender_id: str):
    """Nouvelle conversation pour un utilisateur connecté : nouvel identifiant, ancien supprimé.
    Renvoie (nouveau sender_id, nom, [groupes]) ou None si la session n'est plus valide."""
    r = session_chat(sender_id)
    if not r:
        return None
    nouveau = "u-" + secrets.token_hex(32)
    with _co() as c:
        c.execute("DELETE FROM sessions_chat WHERE sender_id = ?", (sender_id,))
        c.execute("INSERT INTO sessions_chat VALUES (?, ?, ?)", (nouveau, r[0], int(time.time()) + DUREE_SESSION_CHAT))
    return nouveau, r[0], r[1]


def fermer_session_chat(sender_id: str) -> None:
    with _co() as c:
        c.execute("DELETE FROM sessions_chat WHERE sender_id = ?", (sender_id,))


def documents_interdits(sender_id: str) -> set:
    """Chemins des documents restreints que CET interlocuteur n'a PAS le droit d'utiliser.
    Interlocuteur anonyme ou session invalide : tous les documents restreints."""
    with _co() as c:
        r = c.execute("SELECT s.utilisateur FROM sessions_chat s JOIN utilisateurs u ON u.nom = s.utilisateur "
                      "WHERE s.sender_id = ? AND s.expire > ? AND u.actif = 1",
                      (sender_id or "", int(time.time()))).fetchone()
        mes = {x[0] for x in c.execute("SELECT groupe_id FROM membres WHERE utilisateur = ?", (r["utilisateur"],))} if r else set()
        restreints = {}
        for x in c.execute("SELECT doc, groupe_id FROM doc_groupes"):
            restreints.setdefault(x["doc"], set()).add(x["groupe_id"])
    return {d for d, gs in restreints.items() if not (gs & mes)}
