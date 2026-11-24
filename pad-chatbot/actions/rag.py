"""Moteur de recherche documentaire (retrieval du RAG) : recherche hybride.

Trois classements sont fusionnés (Reciprocal Rank Fusion) :
  1. embeddings denses (multilingual-e5) : comprend le sens et les reformulations ;
  2. TF-IDF sur les mots : retrouve les termes exacts (« DP World », « 2022 ») ;
  3. TF-IDF sur les caractères : tolère le pluriel et les fautes (« vehicule » / « véhicules »).

Même interface : MOTEUR.chercher(question, k) renvoie
[((source, texte), score_dense), ...]. On garde les k meilleurs extraits de chaque classement
(donc entre k et 3k extraits, triés par score de fusion). Le score renvoyé est le cosinus dense
(utile pour juger la proximité réelle).
Les tableaux de chiffres, tables des matières et pages trop courtes sont écartés,
et les extraits qui se chevauchent ne sont pas renvoyés deux fois.
Mettre RAG_DEBUG=1 pour voir les scores de chaque classement dans la console.
"""
import hashlib
import os
import re
import threading
import unicodedata
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.feature_extraction.text import TfidfVectorizer

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
CACHE_DIR = Path(__file__).resolve().parent.parent / ".rag_cache"

MODELE = os.getenv("RAG_MODEL", "intfloat/multilingual-e5-small")
# Cosinus dense à partir duquel un extrait est jugé « plausible » (scores E5 compressés : 0.75 à 0.90).
# Sert à l'affichage de secours quand le LLM est indisponible.
SEUIL = float(os.getenv("RAG_SEUIL", "0.80"))
DEBUG = os.getenv("RAG_DEBUG") == "1"

FENETRE = 5     # phrases par morceau
PAS = 3         # deux morceaux consécutifs se chevauchent sur 2 phrases
MIN_MOTS = 25   # écarte les en-têtes et pages de garde
RRF_K = 60      # constante classique de la fusion de classements

STOP = ["le", "la", "les", "de", "du", "des", "un", "une", "et", "en", "au", "aux", "que", "qui",
        "quoi", "est", "sont", "pour", "dans", "sur", "par", "ce", "ces", "quel", "quelle", "quels",
        "quelles", "dit", "vos", "votre", "il", "qu", "moi", "donne", "parle", "combien"]


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFD", t.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _points_rrf(scores: np.ndarray, ignorer_nuls: bool = False) -> np.ndarray:
    """Points de fusion selon le rang de chaque morceau dans ce classement."""
    ordre = np.argsort(-scores, kind="stable")
    rang = np.empty(len(scores), dtype=np.int64)
    rang[ordre] = np.arange(len(scores))
    points = 1.0 / (RRF_K + rang + 1)
    if ignorer_nuls:                      # aucun mot en commun : ce classement ne vote pas
        points = np.where(scores > 0, points, 0.0)
    return points


class MoteurDocs:
    def __init__(self):
        self.modele = SentenceTransformer(MODELE)
        self.verrou = threading.Lock()
        self._charger()

    def _charger(self):
        """(Re)construit tout l'index à partir du dossier docs/."""
        self.morceaux = []    # (source, texte)
        self.positions = []   # (fichier, position de la fenêtre) pour détecter les chevauchements
        self._decouper()
        self.matrice = self._charger_ou_calculer_embeddings()
        self._construire_lexical()
        self.signature = self._signature()

    @staticmethod
    def _signature():
        """Empreinte légère du dossier docs/ (nom, taille, date) : change dès qu'un document change."""
        return tuple((str(f), f.stat().st_size, f.stat().st_mtime_ns)
                     for f in sorted(DOCS_DIR.rglob("*")) if f.suffix.lower() in (".txt", ".md"))

    def recharger_si_besoin(self):
        if self._signature() == self.signature:
            return
        with self.verrou:                      # un seul rechargement à la fois
            if self._signature() != self.signature:
                print("[RAG] documents modifiés : reconstruction de l'index")
                self._charger()

    @staticmethod
    def _utile(texte: str) -> bool:
        """Écarte les tables des matières, les tableaux de chiffres et les textes trop courts."""
        if texte.count("....") >= 3 or len(texte.split()) < MIN_MOTS:
            return False
        lettres = sum(c.isalpha() for c in texte)
        chiffres = sum(c.isdigit() for c in texte)
        return lettres >= 2.5 * chiffres

    def _decouper(self):
        for f in sorted(DOCS_DIR.rglob("*")):
            if f.suffix.lower() not in (".txt", ".md"):
                continue

            contenu = f.read_text(encoding="utf-8")
            url = None                      # page web collectée : 1re ligne « URL: ... », source = l'adresse
            m_url = re.match(r"(?:URL|SOURCE): ([^\n]+)\n(?:TITRE: [^\n]*\n)?", contenu)
            if m_url:
                url, contenu = m_url.group(1).strip(), contenu[m_url.end():].lstrip()
            nom = str(f.relative_to(DOCS_DIR))
            page = "?"
            phrases_buffer = []

            for bloc in re.split(r"\n\s*\n", contenu):
                m = re.match(r"\[Page (\d+)\]", bloc)
                if m:
                    page = m.group(1)
                texte = re.sub(r"\[Page \d+\]\n?", "", bloc)
                texte = re.sub(r"\s*\n\s*", " ", texte).strip()
                for ph in re.split(r"(?<=[.!?;:])\s+", texte):   # « ; » et « : » coupent aussi (listes sans points)
                    if ph:
                        phrases_buffer.append((page, ph))

            for i in range(0, len(phrases_buffer), PAS):
                fenetre = phrases_buffer[i:i + FENETRE]
                texte_chunk = " ".join(p for _, p in fenetre)
                if len(texte_chunk) > 60 and self._utile(texte_chunk):
                    p0 = fenetre[0][0]
                    source = (url if p0 == "?" else f"{url}, p.{p0}") if url else f"{f.name}, p.{p0}"
                    self.morceaux.append((source, texte_chunk))
                    self.positions.append((nom, i))

    # ---------- embeddings (cache disque) ----------
    def _cle(self, source: str, texte: str) -> str:
        return hashlib.sha256(f"{MODELE}\x00{source}\x00{texte}".encode()).hexdigest()[:20]

    def _charger_ou_calculer_embeddings(self) -> np.ndarray:
        """Cache par morceau : un nouveau document ne recalcule que ses propres morceaux."""
        if not self.morceaux:
            return np.empty((0, 0), dtype=np.float32)

        CACHE_DIR.mkdir(exist_ok=True)
        fichier = CACHE_DIR / "embeddings.npz"
        connus = {}
        if fichier.exists():
            try:
                with np.load(fichier, allow_pickle=False) as z:
                    connus = dict(zip(z["cles"].tolist(), z["vecteurs"]))
            except Exception:
                connus = {}

        cles = [self._cle(src, t) for src, t in self.morceaux]
        manquants = [i for i, c in enumerate(cles) if c not in connus]
        if manquants:
            neufs = self.modele.encode(
                ["passage: " + self.morceaux[i][1] for i in manquants],
                normalize_embeddings=True, batch_size=32, show_progress_bar=False,
            ).astype(np.float32)
            for i, v in zip(manquants, neufs):
                connus[cles[i]] = v
        matrice = np.stack([connus[c] for c in cles]).astype(np.float32)
        if manquants or fichier.exists() is False:
            np.savez(fichier, cles=np.array(cles), vecteurs=matrice)
        return matrice

    # ---------- index par mots-clés (rapide : recalculé à chaque démarrage) ----------
    def _construire_lexical(self):
        if not self.morceaux:
            return
        textes = [t for _, t in self.morceaux]
        self.vec_mots = TfidfVectorizer(
            preprocessor=_norm, stop_words=[_norm(s) for s in STOP], ngram_range=(1, 2))
        self.mat_mots = self.vec_mots.fit_transform(textes)
        self.vec_car = TfidfVectorizer(
            preprocessor=_norm, analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
        self.mat_car = self.vec_car.fit_transform(textes)

    # ---------- recherche ----------
    def chercher(self, question: str, k: int = 3, interdits=None):
        """k = nombre d'extraits retenus par classement : on renvoie entre k et 3k extraits.
        interdits = chemins relatifs (ex. « uploads/rh.txt ») des documents que l'interlocuteur n'a pas
        le droit de voir : leurs extraits sont exclus AVANT le classement (ils ne peuvent donc jamais
        être envoyés au LLM ni cités comme source)."""
        self.recharger_si_besoin()
        with self.verrou:                      # pas de lecture pendant une reconstruction
            return self._chercher(question, k, interdits)

    def _chercher(self, question: str, k: int, interdits=None):
        if not self.morceaux:
            return []
        interdits = {str(d).replace("\\", "/") for d in (interdits or ())}
        permis = np.array([self.positions[i][0].replace("\\", "/") not in interdits
                           for i in range(len(self.morceaux))], dtype=bool)
        if not permis.any():
            return []

        q = self.modele.encode(
            ["query: " + question], normalize_embeddings=True
        ).astype(np.float32)[0]
        dense = self.matrice @ q
        mots = (self.mat_mots @ self.vec_mots.transform([question]).T).toarray().ravel()
        car = (self.mat_car @ self.vec_car.transform([question]).T).toarray().ravel()
        dense = np.where(permis, dense, -9.0)      # extraits interdits : jamais candidats
        mots = np.where(permis, mots, -9.0)
        car = np.where(permis, car, -9.0)

        fusion = _points_rrf(dense) + _points_rrf(mots, True) + _points_rrf(car, True)
        ordre = np.argsort(-fusion, kind="stable")

        # Candidats : les k meilleurs de CHAQUE classement (au plus 3k morceaux). Un extrait très bien
        # classé par un seul classement (ex. le mot exact « véhicules ») ne doit pas être écarté parce
        # que les autres le classent mal ; le LLM fera le tri ensuite.
        nb_ok = int(permis.sum())
        tops = [np.argsort(-s, kind="stable")[:min(k, nb_ok)] for s in (dense, mots, car)]
        obligatoires = [int(t[0]) for t in tops]          # le n°1 de chaque classement est toujours gardé
        candidats = {int(i) for t in tops for i in t}
        candidats = [int(i) for i in ordre if int(i) in candidats]   # triés par score de fusion

        if DEBUG:
            print(f"[RAG] question : {question}")
            for i in candidats:
                print(f"[RAG]   fusion={fusion[i]:.4f} dense={dense[i]:.3f} "
                      f"mots={mots[i]:.3f} car={car[i]:.3f}  {self.morceaux[i][0]}  [fichier : {self.positions[i][0]}]")

        retenus = []
        for i in candidats:
            fichier, pos = self.positions[i]
            # on saute un morceau qui chevauche un morceau déjà retenu (sauf les n°1 des classements)
            if i not in obligatoires and any(
                    fichier == self.positions[j][0] and abs(pos - self.positions[j][1]) <= PAS
                    for j in retenus):
                continue
            retenus.append(i)

        return [(self.morceaux[i], float(dense[i])) for i in retenus]


MOTEUR = MoteurDocs()
