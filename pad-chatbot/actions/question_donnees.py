"""Questions libres sur les navires : le LLM écrit un SELECT, db.py le vérifie et l'exécute en lecture seule.

Garde-fous (db.verifier_select / db.executer_select) : une seule instruction SELECT, uniquement la
table « navires » (jamais les rendez-vous), 20 lignes maximum, 5 s maximum, session en lecture seule.
Pour une vraie protection, faire pointer DATABASE_URL_LECTURE vers un compte SQL qui n'a que le droit SELECT.
"""
import os

from rasa_sdk import Action

from . import db
from .llm import MODELE, SECOURS, _appeler

CONSIGNE = (
    "Tu convertis une question en français en UNE requête SQL SELECT (dialecte {dialecte}). "
    "Schéma : " + db.DESCRIPTION_SCHEMA + " "
    "Réponds uniquement par la requête, sans explication ni balises. Utilise LOWER() et LIKE pour "
    "comparer des textes. Si la question ne peut pas être traitée avec ce schéma, réponds exactement : IMPOSSIBLE"
)

IMPOSSIBLE = ("Je ne peux pas répondre à cette question avec les données de suivi des navires. "
              "Essayez par exemple « combien de navires sont à quai ? ».")


def _generer_sql(question: str):
    cle = os.getenv("GEMINI_API_KEY")
    if not cle:
        return None
    corps = {
        "system_instruction": {"parts": [{"text": CONSIGNE.format(dialecte=db.dialecte(db._url_lecture()))}]},
        "contents": [{"role": "user", "parts": [{"text": question}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 1024},
    }
    for modele in [MODELE] + [m for m in SECOURS if m != MODELE]:
        brut = _appeler(modele, cle, corps)
        if brut:
            return brut
    return None


def _formater(cols, lignes) -> str:
    if not lignes:
        return "Aucun résultat."
    if len(lignes) == 1 and len(cols) == 1:
        return f"Résultat : {lignes[0][0]}"
    return "\n".join("- " + " ; ".join(f"{c} : {v}" for c, v in zip(cols, l)) for l in lignes)


class ActionQuestionDonnees(Action):
    def name(self) -> str:
        return "action_question_donnees"

    def run(self, dispatcher, tracker, domain):
        question = tracker.latest_message.get("text", "")
        sql = _generer_sql(question)
        if sql is None:
            dispatcher.utter_message(text="Le service de questions sur les données est indisponible pour le moment.")
            return []
        if sql.strip().upper().startswith("IMPOSSIBLE"):
            dispatcher.utter_message(text=IMPOSSIBLE)
            return []
        try:
            cols, lignes = db.executer_select(sql)
        except Exception as e:                      # requête refusée par le garde-fou ou erreur SQL
            print(f"[DB] requête refusée ou en erreur : {str(e)[:150]} | {sql[:200]}")
            dispatcher.utter_message(text=IMPOSSIBLE)
            return []
        dispatcher.utter_message(text=_formater(cols, lignes))
        return []
