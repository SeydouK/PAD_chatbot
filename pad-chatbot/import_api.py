"""Importe des textes depuis une API REST (JSON) vers docs/api/ pour le RAG.

Même principe que export_db.py : on décrit l'API dans l'interface d'administration (page « API »),
on importe à la demande, ou on planifie :  python import_api.py --admin

Garde-fous : http(s) uniquement, adresses internes refusées, 5 Mo et 15 s maximum par réponse,
la clé d'API n'est envoyée qu'au site configuré (jamais à un autre hôte, même après redirection).
"""
import argparse
import json
import ipaddress
import os
import re
import socket
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

AGENT = "PAD-chatbot-import-api/1.0"
MAX_OCTETS = 5 * 1024 * 1024
DELAI = 15
MAX_PAGES = 20
MIN_MOTS = 20
ENTETE = re.compile(r"^[A-Za-z0-9-]{1,40}$")


def url_autorisee(url: str) -> bool:
    """http(s) uniquement, et pas d'adresse interne (PAD_SCRAPE_ALLOW_PRIVATE=1 pour les essais locaux)."""
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


def verifier_entete(nom: str, valeur: str) -> None:
    if nom and not ENTETE.match(nom):
        raise ValueError("nom d'en-tête invalide (lettres, chiffres et - uniquement)")
    if valeur and re.search(r"[\r\n]", valeur):
        raise ValueError("valeur de clé invalide")


def _get(url: str, entete: str, valeur: str, hote: str):
    """GET sûr : redirections suivies à la main (3 max, même hôte), taille et durée limitées."""
    for _ in range(4):
        if not url_autorisee(url):
            raise ValueError("adresse refusée (http ou https public uniquement)")
        if urlparse(url).hostname != hote:
            raise ValueError("redirection vers un autre site refusée")
        h = {"User-Agent": AGENT, "Accept": "application/json"}
        if entete and valeur:
            h[entete] = valeur
        try:
            r = requests.get(url, headers=h, timeout=DELAI, stream=True, allow_redirects=False)
        except requests.RequestException as e:
            raise ValueError(f"API injoignable ({e.__class__.__name__})")
        if r.is_redirect:
            url = urljoin(url, r.headers.get("Location", ""))
            continue
        if r.status_code in (401, 403):
            raise ValueError(f"accès refusé par l'API ({r.status_code}) : vérifiez la clé")
        if r.status_code != 200:
            raise ValueError(f"l'API a répondu {r.status_code}")
        brut = r.raw.read(MAX_OCTETS + 1, decode_content=True)
        if len(brut) > MAX_OCTETS:
            raise ValueError("réponse trop volumineuse (5 Mo maximum)")
        try:
            return json.loads(brut.decode("utf-8-sig"))
        except ValueError:
            raise ValueError("la réponse n'est pas du JSON valide")
    raise ValueError("trop de redirections")


def chemin(obj, pointe: str):
    """Navigue dans un objet JSON avec un chemin à points (« data.items », « attributes.title »)."""
    for part in [p for p in (pointe or "").split(".") if p]:
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return None
    return obj


def texte_de(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (str, int, float, bool)):
        t = str(v)
    elif isinstance(v, list):
        t = ", ".join(x for x in (texte_de(e) for e in v) if x)
    elif isinstance(v, dict):
        t = "; ".join(f"{k} : {x}" for k, x in ((k, texte_de(e)) for k, e in v.items()) if x)
    else:
        t = str(v)
    if "<" in t and ">" in t:                     # HTML dans un champ
        from bs4 import BeautifulSoup
        t = re.sub(r"(?i)</(?:p|div|li|h[1-6])\s*>|<br\s*/?>", "\n", t)
        t = BeautifulSoup(t, "html.parser").get_text("")
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\s*\n\s*", "\n", t).strip()


def recuperer(cfg: dict, entete: str = "", valeur: str = "", limite_pages: int = MAX_PAGES):
    """Renvoie la liste des éléments JSON (toutes pages confondues, limitée à max_elements)."""
    verifier_entete(entete, valeur)
    url = cfg["url"]
    hote = urlparse(url).hostname
    pag = cfg.get("pagination") or {}
    maxi = max(1, min(int(cfg.get("max_elements") or 500), 5000))
    elements, vus = [], set()
    for n in range(limite_pages):
        courant = url
        if pag.get("type") == "page":
            sep = "&" if "?" in url else "?"
            courant = f"{url}{sep}{pag.get('param', 'page')}={int(pag.get('debut', 1)) + n}"
        data = _get(courant, entete, valeur, hote)
        liste = chemin(data, cfg.get("chemin_liste", ""))
        if isinstance(liste, dict):
            liste = [liste]
        if not isinstance(liste, list):
            raise ValueError("liste introuvable : vérifiez le « chemin de la liste »")
        nouveaux = [e for e in liste if isinstance(e, dict)]
        if pag.get("type") == "page" and not nouveaux:
            break
        signature = repr(nouveaux[:1])
        if pag.get("type") == "page" and signature in vus:     # l'API renvoie toujours la même page
            break
        vus.add(signature)
        elements += nouveaux
        if len(elements) >= maxi:
            break
        if pag.get("type") == "suivant":
            suivant = chemin(data, pag.get("champ_suivant", "next"))
            if not isinstance(suivant, str) or not suivant:
                break
            url = urljoin(url, suivant)
        elif pag.get("type") != "page":
            break
    return elements[:maxi]


def apercu(elements: list) -> list:
    """Champs (y compris imbriqués, 2 niveaux) du premier élément, avec un exemple, pour guider le choix."""
    if not elements:
        return []
    out = []
    for k, v in elements[0].items():
        if isinstance(v, dict):
            out += [{"nom": f"{k}.{k2}", "exemple": texte_de(v2)[:80]} for k2, v2 in v.items() if not isinstance(v2, (dict, list))]
        else:
            out.append({"nom": k, "exemple": texte_de(v)[:80]})
    return out


def nom_fichier(src: str, ident) -> str:
    sur = re.sub(r"[^A-Za-z0-9_-]+", "-", str(ident)).strip("-")[:60] or "element"
    return f"{src}__{sur}.txt"


def importer(nom: str, cfg: dict, sortie: Path, entete: str = "", valeur: str = "", dry: bool = False) -> int:
    champs = cfg.get("champs_texte") or []
    if not champs:
        raise ValueError("aucun champ de texte choisi")
    elements = recuperer(cfg, entete, valeur)
    ecrits = {}
    for i, e in enumerate(elements, 1):
        ident = texte_de(chemin(e, cfg["champ_id"])) if cfg.get("champ_id") else str(i)
        titre = texte_de(chemin(e, cfg["champ_titre"])) if cfg.get("champ_titre") else ""
        exclus = {cfg.get("champ_id"), cfg.get("champ_titre")}
        paragraphes = list(dict.fromkeys(texte_de(chemin(e, c)) for c in champs if c not in exclus))
        corps = "\n\n".join(p if re.search(r"[.!?:;]$", p) else p + "." for p in paragraphes if p)
        if len((titre + " " + corps).split()) < MIN_MOTS:
            continue
        etiquette = f"{cfg.get('libelle') or 'API ' + nom} n°{ident or i}"
        ecrits[nom_fichier(nom, ident or i)] = (
            f"SOURCE: {etiquette}\nTITRE: {titre}\n\n" + (f"{titre}.\n\n" if titre else "") + corps + "\n")
    if dry:
        return len(ecrits)
    sortie.mkdir(parents=True, exist_ok=True)
    for f, contenu in ecrits.items():
        (sortie / f).write_text(contenu, encoding="utf-8")
    for ancien in sortie.glob(f"{nom}__*.txt"):          # élément disparu de l'API
        if ancien.name not in ecrits:
            ancien.unlink()
    return len(ecrits)


def importer_depuis_admin(sortie: Path, dry: bool = False, id_source: int = None) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from actions import store
    bilan = {}
    for src in store.lister_apis():
        if id_source and src["id"] != id_source:
            continue
        try:
            entete, valeur = store.cle_api(src["id"])
            bilan[src["nom"]] = importer(src["nom"], dict(src["config"], url=src["url"]), sortie, entete, valeur, dry)
        except Exception as e:
            bilan[src["nom"]] = f"erreur : {e.__class__.__name__}: {str(e)[:200]}"
    return bilan


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--admin", action="store_true", required=True, help="importer les API configurées dans l'administration")
    ap.add_argument("--sortie", default="docs/api")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    bilan = importer_depuis_admin(Path(a.sortie), a.dry_run)
    for k, v in bilan.items():
        print(f"{k} : {v}")
    sys.exit(1 if any(isinstance(v, str) for v in bilan.values()) else 0)


if __name__ == "__main__":
    main()
