"""Reformulation des passages retrouvés par le RAG avec Gemini (API REST).

Sans dépendance supplémentaire : utilise `requests`, déjà installé avec Rasa.
Clé lue dans la variable d'environnement GEMINI_API_KEY (jamais dans le code).

reformuler() renvoie :
  - None si le LLM est indisponible (l'action affiche alors un repli) ;
  - (texte, utilises) sinon, où `utilises` est la liste des numéros (à partir de 1) des extraits
    réellement utilisés, [] si le LLM estime que les extraits ne contiennent pas la réponse,
    et None s'il n'a pas indiqué ses sources.
"""
import os
import re
import time
from typing import List, Optional, Tuple

import requests

MODELE = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
SECOURS = ["gemini-3.5-flash", "gemini-3.8-flash"]    # gemini-2.5-flash n'est plus offert aux nouveaux comptes (404)
URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
TIMEOUT = 25    # un modèle qui ne répond pas en 25 s : on passe au suivant (le widget attend 90 s au total)
MAX_TOKENS = 2048   # les modèles récents comptent leur réflexion dans cette limite
SURCHARGE = (429, 500, 503)

CONSIGNE = (
    "Tu es l'assistant du Port Autonome de Dakar. Réponds en français, en 2 à 4 phrases, "
    "uniquement à partir des extraits fournis. Les extraits viennent de documents de dates "
    "différentes (bulletin 2015, analyse 2020, statistiques 2022) : précise l'année quand "
    "c'est utile. Si les extraits ne contiennent pas la réponse, dis-le simplement sans "
    "inventer. Ne recopie pas les extraits mot pour mot. "
    "Termine toujours par une dernière ligne « SOURCES: » suivie des numéros des extraits "
    "que tu as réellement utilisés, séparés par des virgules (par exemple « SOURCES: 1, 3 »), "
    "ou « SOURCES: aucune » si les extraits ne permettent pas de répondre."
)

_RE_SOURCES = re.compile(r"^\W*SOURCES?\s*:\s*(.*?)\s*$", re.IGNORECASE | re.MULTILINE)

if not os.getenv("GEMINI_API_KEY"):
    print("[LLM] GEMINI_API_KEY absente de l'environnement")


def extraire_sources(texte: str, nb_extraits: int) -> Tuple[str, Optional[List[int]]]:
    """Sépare la réponse de la ligne « SOURCES: ... » et renvoie (réponse, numéros utilisés)."""
    derniere = None
    for derniere in _RE_SOURCES.finditer(texte):
        pass
    if derniere is None:
        return texte.strip(), None
    corps = texte[:derniere.start()].strip()
    reste = derniere.group(1).lower()
    if "aucune" in reste or "none" in reste:
        return corps, []
    numeros = sorted({int(n) for n in re.findall(r"\d+", reste) if 1 <= int(n) <= nb_extraits})
    return corps, (numeros or None)


def _appeler(modele: str, cle: str, corps: dict) -> Optional[str]:
    for essai in range(3):
        try:
            r = requests.post(
                URL.format(modele),
                headers={"x-goog-api-key": cle, "Content-Type": "application/json"},
                json=corps,
                timeout=TIMEOUT,
            )
            if r.status_code in SURCHARGE and essai < 2:
                print(f"[LLM] {modele} HTTP {r.status_code}, nouvel essai dans {2 * (essai + 1)} s")
                time.sleep(2 * (essai + 1))
                continue
            r.raise_for_status()
            candidat = r.json()["candidates"][0]
            if candidat.get("finishReason") == "MAX_TOKENS":
                print(f"[LLM] {modele} : réponse tronquée (MAX_TOKENS)")
            return candidat["content"]["parts"][0]["text"].strip()
        except requests.exceptions.Timeout as e:
            print(f"[LLM] {modele} trop lent (> {TIMEOUT} s), modèle suivant")
            return None
        except Exception as e:
            detail = getattr(getattr(e, "response", None), "text", "")
            print(f"[LLM] {modele} erreur : {repr(e)[:120]} {detail[:200]}")
            return None
    return None


def reformuler(question: str, passages: List[str]) -> Optional[Tuple[str, Optional[List[int]]]]:
    cle = os.getenv("GEMINI_API_KEY")
    if not cle or not passages:
        return None

    extraits = "\n\n".join(f"[Extrait {i + 1}] {p}" for i, p in enumerate(passages))
    corps = {
        "system_instruction": {"parts": [{"text": CONSIGNE}]},
        "contents": [{
            "role": "user",
            "parts": [{"text": f"Question : {question}\n\nExtraits :\n{extraits}"}],
        }],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": MAX_TOKENS},
    }

    for modele in [MODELE] + [m for m in SECOURS if m != MODELE]:
        if "2.5" in modele:                      # modèles 2.5 : on coupe la « réflexion » (réponse bien plus rapide)
            corps["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
        else:
            corps["generationConfig"].pop("thinkingConfig", None)
        brut = _appeler(modele, cle, corps)
        if not brut:
            continue
        if os.getenv("RAG_DEBUG") == "1":
            print(f"[LLM] {modele} a répondu : {brut[:400]!r}")
            for i, p in enumerate(passages, 1):
                print(f"[LLM]   extrait {i} ({len(p.split())} mots) : {p[:90]!r} … {p[-60:]!r}")
        texte, utilises = extraire_sources(brut, len(passages))
        if texte or utilises == []:
            return texte, utilises
    return None
