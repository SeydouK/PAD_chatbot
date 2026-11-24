"""Accès à la base de données (MySQL, PostgreSQL ou SQLite) pour les actions du chatbot.

Choix de la base par variables d'environnement, à définir dans le terminal de `rasa run actions` :

  DATABASE_URL          base principale (lecture/écriture : navires, rendez-vous)
      PostgreSQL : postgresql+psycopg2://utilisateur:motdepasse@localhost:5432/pad
      MySQL      : mysql+pymysql://utilisateur:motdepasse@localhost:3306/pad
      SQL Server : mssql+pymssql://utilisateur:motdepasse@localhost:1433/pad
      (absente)  : fichier SQLite local pad.db, pratique pour la démo

  DATABASE_URL_LECTURE  (facultative) compte en LECTURE SEULE, utilisé uniquement pour les questions
                        libres sur les données. Si absente, on utilise DATABASE_URL avec une
                        transaction en lecture seule.

Pilotes à installer :  pip install psycopg2-binary   et/ou   pip install pymysql   et/ou   pip install pymssql
"""
import os
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, text

_moteurs = {}
_prets = set()


def _url() -> str:
    """Base principale : variable DATABASE_URL, sinon connexion « principale » choisie dans l'interface
    d'administration (actions/store.py), sinon fichier SQLite local pad.db."""
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    try:
        from . import store
        u = store.url_principale()
        if u:
            return u
    except Exception as e:
        print(f"[DB] configuration d'administration illisible : {str(e)[:150]}")
    return f"sqlite:///{Path(__file__).resolve().parent.parent / 'pad.db'}"


def _url_lecture() -> str:
    return os.getenv("DATABASE_URL_LECTURE") or _url()


def _moteur(url: str, lecture: bool = False):
    """Moteur de connexions. Celui de lecture est distinct : ses sessions sont mises en lecture seule."""
    cle = (url, lecture)
    if cle not in _moteurs:
        _moteurs[cle] = create_engine(url, pool_pre_ping=True, pool_recycle=1800)
    if not lecture and url not in _prets:         # première utilisation de cette base : tables + démo
        _prets.add(url)
        try:
            init(url)
        except Exception:
            _prets.discard(url)               # base injoignable : on réessaiera à la prochaine demande
            raise
    return _moteurs[cle]


def dialecte(url: str) -> str:
    return url.split(":", 1)[0].split("+", 1)[0]     # sqlite, postgresql, mysql


# ---------- création des tables et données de démonstration ----------
SCHEMA = """
CREATE TABLE IF NOT EXISTS navires (
    nom        VARCHAR(100) PRIMARY KEY,
    statut     VARCHAR(30)  NOT NULL,
    poste      VARCHAR(50),
    eta        VARCHAR(120),
    cargaison  VARCHAR(100)
)""", """
CREATE TABLE IF NOT EXISTS rendez_vous (
    id         {auto},
    cree_le    VARCHAR(25) NOT NULL,
    nom        VARCHAR(100) NOT NULL,
    email      VARCHAR(150) NOT NULL,
    motif      VARCHAR(500) NOT NULL
)"""

# SQL Server n'a pas « CREATE TABLE IF NOT EXISTS » et préfère NVARCHAR (accents)
SCHEMA_MSSQL = """
IF OBJECT_ID('navires', 'U') IS NULL CREATE TABLE navires (
    nom        NVARCHAR(100) PRIMARY KEY,
    statut     NVARCHAR(30)  NOT NULL,
    poste      NVARCHAR(50),
    eta        NVARCHAR(120),
    cargaison  NVARCHAR(100)
)""", """
IF OBJECT_ID('rendez_vous', 'U') IS NULL CREATE TABLE rendez_vous (
    id         INT IDENTITY(1,1) PRIMARY KEY,
    cree_le    NVARCHAR(25)  NOT NULL,
    nom        NVARCHAR(100) NOT NULL,
    email      NVARCHAR(150) NOT NULL,
    motif      NVARCHAR(500) NOT NULL
)"""

DEMO = [
    ("atlantic star", "à quai", "Poste 12", "déjà arrivé", "conteneurs"),
    ("gulf pearl", "en rade", "en attente", "accostage prévu demain à 08h00", "hydrocarbures"),
    ("sahel express", "en route", "-", "arrivée prévue dans 2 jours", "riz en vrac"),
]


def init(url: str = None) -> None:
    """Crée les tables si besoin et insère les 3 navires de démonstration dans une table vide."""
    url = url or _url()
    _prets.add(url)
    d = dialecte(url)
    auto = {"postgresql": "SERIAL PRIMARY KEY", "mysql": "INT AUTO_INCREMENT PRIMARY KEY"}.get(
        d, "INTEGER PRIMARY KEY AUTOINCREMENT")
    with _moteur(url).begin() as c:
        for ddl in (SCHEMA_MSSQL if d == "mssql" else SCHEMA):
            c.execute(text(ddl.format(auto=auto)))
        if c.execute(text("SELECT COUNT(*) FROM navires")).scalar() == 0:
            for n in DEMO:
                c.execute(text("INSERT INTO navires (nom, statut, poste, eta, cargaison) "
                               "VALUES (:n, :s, :p, :e, :c)"),
                          dict(zip("nspec", n)))


# ---------- navires ----------
def navire(nom: str):
    with _moteur(_url()).connect() as c:
        r = c.execute(text("SELECT nom, statut, poste, eta, cargaison FROM navires WHERE nom = :n"),
                      {"n": nom}).mappings().first()
    return dict(r) if r else None


def noms_navires():
    with _moteur(_url()).connect() as c:
        return [r[0] for r in c.execute(text("SELECT nom FROM navires ORDER BY nom"))]


# ---------- rendez-vous ----------
def enregistrer_rdv(nom: str, email: str, motif: str) -> None:
    with _moteur(_url()).begin() as c:
        c.execute(text("INSERT INTO rendez_vous (cree_le, nom, email, motif) VALUES (:d, :n, :e, :m)"),
                  {"d": datetime.now().isoformat(timespec="seconds"), "n": nom, "e": email, "m": motif})


# ---------- questions libres : SELECT contrôlé ----------
TABLES_AUTORISEES = {"navires"}        # jamais rendez_vous (données personnelles)
MAX_LIGNES = 20
INTERDITS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|replace|merge|call|exec|execute|"
    r"copy|load|into|outfile|dumpfile|pg_sleep|sleep|benchmark|set|lock|attach|pragma|vacuum|"
    r"waitfor|bulk|openrowset|opendatasource|openquery|shutdown|dbcc|backup|restore|xp_\w*|sp_\w*)\b", re.I)


def verifier_select(sql: str) -> str:
    """Renvoie la requête nettoyée, ou lève ValueError. Une seule instruction SELECT, tables autorisées."""
    s = re.sub(r"^\s*```(?:sql)?|```\s*$", "", sql.strip(), flags=re.I).strip().rstrip(";").strip()
    if not s or ";" in s or "--" in s or "/*" in s:
        raise ValueError("requête vide ou multiple")
    if not re.match(r"(?i)^select\b", s):
        raise ValueError("seul SELECT est autorisé")
    if INTERDITS.search(s):
        raise ValueError("mot-clé interdit")
    tables = {t.lower().strip('`"') for t in re.findall(r"(?i)\b(?:from|join)\s+([`\"\w.]+)", s)}
    if not tables or not tables <= TABLES_AUTORISEES:
        raise ValueError(f"table non autorisée : {sorted(tables - TABLES_AUTORISEES) or 'aucune'}")
    return s


def executer_select(sql: str):
    """Exécute un SELECT vérifié, en transaction lecture seule, limité à MAX_LIGNES lignes."""
    s = verifier_select(sql)
    url_l = _url_lecture()
    d = dialecte(url_l)
    with _moteur(url_l, lecture=True).connect() as c:
        # réglages de SESSION (ce moteur ne sert qu'aux lectures) : fonctionnent en SQLAlchemy 1.4 et 2.0
        if d == "postgresql":
            c.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
            c.execute(text("SET statement_timeout = 5000"))
        elif d == "mysql":
            c.execute(text("SET SESSION TRANSACTION READ ONLY"))
            c.execute(text("SET SESSION MAX_EXECUTION_TIME = 5000"))
        elif d == "sqlite":
            c.execute(text("PRAGMA query_only = ON"))
        if hasattr(c, "commit"):
            c.commit()                       # SQLAlchemy 2.0 : termine la transaction ouverte par les SET
        # limite faite côté Python : fonctionne pareil sur toutes les bases (SQL Server n'a pas LIMIT)
        res = c.execute(text(s))
        cols = list(res.keys())
        lignes = [list(r) for r in res.fetchmany(MAX_LIGNES)]
        res.close()
        if hasattr(c, "rollback"):
            c.rollback()
    return cols, lignes


DESCRIPTION_SCHEMA = (
    "Table navires(nom TEXT, statut TEXT, poste TEXT, eta TEXT, cargaison TEXT). "
    "statut vaut 'à quai', 'en rade' ou 'en route'. Les noms sont en minuscules."
)
