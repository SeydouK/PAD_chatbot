"""Exporte des textes stockés en base (MySQL, PostgreSQL, SQLite) vers docs/db/ pour le RAG.

Même principe que scrape_site.py : le script tourne hors ligne (à la main ou planifié), écrit un fichier
texte par ligne de table, et l'index du chatbot se reconstruit tout seul au prochain message.

Usage (dossier pad-chatbot) :
    python export_db.py                      # lit export_db.yml
    python export_db.py --config autre.yml --dry-run

Les mots de passe ne sont JAMAIS dans export_db.yml : on y met le NOM d'une variable d'environnement
(url_env) qui contient l'URL de connexion, comme DATABASE_URL (voir db.py).
N'exportez pas de tables contenant des données personnelles (rendez_vous, comptes...) : tout ce qui est
exporté devient consultable par n'importe quel visiteur du chatbot.
"""
import argparse
import os
import re
import sys
from pathlib import Path

import yaml
from sqlalchemy import create_engine, text

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MIN_MOTS = 20


def verifier_ident(valeur: str, role: str) -> str:
    if not isinstance(valeur, str) or not IDENT.match(valeur):
        raise ValueError(f"Nom de {role} invalide : {valeur!r} (lettres, chiffres et _ uniquement)")
    return valeur


def nettoyer(valeur) -> str:
    if valeur is None:
        return ""
    t = str(valeur)
    if "<" in t and ">" in t:                     # HTML (corps d'article Drupal, par exemple)
        from bs4 import BeautifulSoup
        t = re.sub(r"(?i)</(?:p|div|li|h[1-6]|tr)\s*>|<br\s*/?>", "\n", t)   # fins de blocs = retours à la ligne
        t = BeautifulSoup(t, "html.parser").get_text("")
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\s*\n\s*", "\n", t).strip()


def nom_fichier(src: str, ident) -> str:
    sûr = re.sub(r"[^A-Za-z0-9_-]+", "-", str(ident)).strip("-")[:60] or "ligne"
    return f"{src}__{sûr}.txt"


def moteur_lecture(url: str):
    """Moteur dont les sessions sont en lecture seule (le script n'écrit jamais en base)."""
    e = create_engine(url, pool_pre_ping=True)
    dial = url.split(":", 1)[0].split("+", 1)[0]
    if dial == "postgresql":
        sql = ["SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"]
    elif dial == "mysql":
        sql = ["SET SESSION TRANSACTION READ ONLY"]
    elif dial == "sqlite":
        sql = ["PRAGMA query_only = ON"]
    else:
        sql = []
    return e, sql


def exporter(source: dict, sortie: Path, dry: bool, url: str = None) -> int:
    """url : URL de connexion deja resolue (interface admin) ; sinon variable url_env."""
    nom = verifier_ident(source.get("nom", ""), "source")
    table = verifier_ident(source.get("table", ""), "table")
    ident = verifier_ident(source.get("id", "id"), "colonne id")
    titre = verifier_ident(source["titre"], "colonne titre") if source.get("titre") else None
    colonnes = [verifier_ident(c, "colonne") for c in source.get("colonnes", [])]
    if not colonnes:
        raise ValueError(f"Source {nom} : « colonnes » est vide")
    url = url or os.getenv(source.get("url_env", "DATABASE_URL_LECTURE")) or os.getenv("DATABASE_URL")
    if not url:
        raise ValueError(f"Source {nom} : variable {source.get('url_env', 'DATABASE_URL_LECTURE')} absente")
    maxi = int(source.get("max_lignes", 500))
    cols = [ident] + ([titre] if titre else []) + colonnes
    mssql = url.startswith("mssql")                # SQL Server : « TOP n » au lieu de « LIMIT n »
    sql = f"SELECT {'TOP ' + str(maxi) + ' ' if mssql else ''}{', '.join(dict.fromkeys(cols))} FROM {table}"
    params = {}
    filtre = source.get("filtre")                  # filtre simple et sûr : colonne = valeur (valeur liée)
    if filtre and filtre.get("colonne"):
        sql += f" WHERE {verifier_ident(filtre['colonne'], 'colonne du filtre')} = :filtre"
        params["filtre"] = filtre.get("valeur")
    elif source.get("where"):                      # fichier YAML écrit à la main uniquement
        sql += f" WHERE {source['where']}"
    sql += f" ORDER BY {ident}" + ("" if mssql else f" LIMIT {maxi}")

    moteur, reglages = moteur_lecture(url)
    ecrits = {}
    with moteur.connect() as c:
        for r in reglages:
            c.execute(text(r))
        if hasattr(c, "commit"):
            c.commit()
        lignes = c.execute(text(sql), params).mappings().all()
    for l in lignes:
        t = nettoyer(l[titre]) if titre else ""
        exclues = {c for c in (titre, ident, (source.get("filtre") or {}).get("colonne")) if c}
        paragraphes = list(dict.fromkeys(nettoyer(l[col]) for col in colonnes if col not in exclues))
        corps = "\n\n".join(p if re.search(r"[.!?:;]$", p) else p + "." for p in paragraphes if p)
        if len((t + " " + corps).split()) < MIN_MOTS:
            continue
        etiquette = source.get("libelle", f"base de données, {table}") + f" n°{l[ident]}"
        entete = f"SOURCE: {etiquette}\nTITRE: {t}\n\n"
        ecrits[nom_fichier(nom, l[ident])] = entete + (f"{t}.\n\n" if t else "") + corps + "\n"

    print(f"{nom} : {len(lignes)} ligne(s) lue(s), {len(ecrits)} fichier(s) à écrire")
    if dry:
        return len(ecrits)
    sortie.mkdir(parents=True, exist_ok=True)
    for f, contenu in ecrits.items():
        (sortie / f).write_text(contenu, encoding="utf-8")
    for ancien in sortie.glob(f"{nom}__*.txt"):          # ligne supprimée ou dépubliée
        if ancien.name not in ecrits:
            ancien.unlink()
    return len(ecrits)


def exporter_depuis_admin(sortie: Path, dry: bool = False, id_source: int = None) -> dict:
    """Lance les exports configures dans l'interface admin. Renvoie {export: nombre ou message d'erreur}."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from actions import store
    bilan = {}
    for src in store.lister_sources():
        if id_source and src["id"] != id_source:
            continue
        for ex in src["exports"]:
            etiquette = f"{src['nom']}__{ex.get('table', '?')}"
            try:
                cfg = dict(ex, nom=etiquette)
                bilan[etiquette] = exporter(cfg, sortie, dry, url=store.url_source(src["id"]))
            except Exception as e:
                bilan[etiquette] = f"erreur : {e.__class__.__name__}: {str(e)[:200]}"
    return bilan


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="export_db.yml")
    ap.add_argument("--sortie", default="docs/db")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--admin", action="store_true",
                    help="exporter les tables configurees dans l'interface d'administration (planifiable)")
    a = ap.parse_args()
    if a.admin:
        bilan = exporter_depuis_admin(Path(a.sortie), a.dry_run)
        for k, v in bilan.items():
            print(f"{k} : {v}")
        sys.exit(1 if any(isinstance(v, str) for v in bilan.values()) else 0)
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8")) or {}
    sources = cfg.get("sources") or []
    if not sources:
        sys.exit(f"Aucune source dans {a.config}")
    try:
        total = sum(exporter(s, Path(a.sortie), a.dry_run) for s in sources)
    except ValueError as e:
        sys.exit(str(e))
    print(f"Terminé : {total} fichier(s)" + (" (essai, rien écrit)" if a.dry_run else f" dans {a.sortie}"))


if __name__ == "__main__":
    main()
