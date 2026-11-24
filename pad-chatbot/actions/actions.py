import os
import re
import unicodedata

from . import db, store
from .llm import reformuler
from .rag import MOTEUR, SEUIL

from typing import Any, Text, Dict, List 

from rasa_sdk import Action, FormValidationAction, Tracker 
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.types import DomainDict
from rasa_sdk.events import SlotSet

HORAIRES = {
    "lundi": "de 8h à 17h",
    "mardi": "de 8h à 17h",
    "mercredi": "de 8h à 17h",
    "jeudi": "de 8h à 17h",
    "vendredi": "de 8h à 13h",
    "samedi": None,     
    "dimanche": None,    
}

SERVICES = {
    "pilotage": "Le pilotage consiste à guider les navires dans le chenal d'accès et jusqu'à leur poste à quai, avec un pilote à bord.",
    "remorquage": "Le remorquage utilise des remorqueurs pour assister les navires dans leurs manœuvres d'accostage et d'appareillage.",
    "lamanage": "Le lamanage consiste à amarrer et désamarrer les navires à quai.",
    "manutention": "La manutention regroupe le chargement, le déchargement et le déplacement des marchandises (conteneurs, vrac, etc.).",
    "entreposage": "L'entreposage permet de stocker les marchandises dans des hangars ou sur des aires dédiées avant leur enlèvement ou leur embarquement.",
}
ALIAS_SERVICES = {"pilote": "pilotage", "remorqueur": "remorquage",
                  "stockage": "entreposage", "amarrage": "lamanage"}

PAS_TROUVE = ("Je n'ai pas trouvé cette information dans la documentation. "
              "Vous pouvez reformuler votre question ou demander un rendez-vous.")


def _normaliser(texte: str) -> str:
    texte = unicodedata.normalize("NFD", str(texte).lower().strip())
    return "".join(c for c in texte if unicodedata.category(c) != "Mn")

class ActionInfoService(Action):

    def name(self) -> Text:
        return "action_info_service"

    def run(
        self, 
        dispatcher, 
        tracker, 
        domain
        ):
        service = tracker.get_slot("service")
        if service:
            cle = _normaliser(service)
            cle = ALIAS_SERVICES.get(cle, cle)
            if cle in SERVICES:
                dispatcher.utter_message(text=SERVICES[cle])
            else:
                dispatcher.utter_message(
                    text=f"Je n'ai pas d'information sur « {service} ». "
                         f"Services disponibles : {', '.join(SERVICES)}.")
        else:
            liste = "\n".join(f"- {s.capitalize()}" for s in SERVICES)
            dispatcher.utter_message(
                text="Voici nos principaux services :\n" + liste +
                     "\nDemandez-moi le détail d'un service, par exemple « c'est quoi le remorquage ? »")
        return [SlotSet("service", None)]

class ActionRagDocs(Action):
 
    def name(self) -> Text:
        return "action_rag_docs"
 
    def run(self, dispatcher, tracker, domain):
        question = tracker.latest_message.get("text", "")
        try:
            # documents réservés à des groupes : exclus si l'interlocuteur n'est pas connecté / pas membre
            interdits = store.documents_interdits(tracker.sender_id)
        except Exception as e:                       # en cas de doute on ne montre rien de restreint
            print(f"[RAG] droits illisibles : {str(e)[:150]}")
            dispatcher.utter_message(text="Service momentanément indisponible, réessayez dans un instant.")
            return []
        if os.getenv("RAG_DEBUG") == "1":
            print(f"[RAG] interlocuteur : {tracker.sender_id[:12]}… ; documents interdits pour lui : {sorted(interdits)}")
        resultats = MOTEUR.chercher(question, k=6, interdits=interdits)   # jusqu'à 18 extraits : le LLM fait le tri
 
        if not resultats:
            dispatcher.utter_message(text=PAS_TROUVE)
            return []
 
        vus, uniques = set(), []                     # même texte sur deux adresses : un seul exemplaire
        for r in resultats:
            if r[0][1] not in vus:
                vus.add(r[0][1])
                uniques.append(r)
        resultats = uniques[:10]                     # moins de texte = Gemini plus rapide
        passages = [texte for (_, texte), _ in resultats]
        reponse = reformuler(question, passages)
 
        if reponse is not None:
            texte, utilises = reponse
            if utilises == []:                       # le LLM dit que les extraits ne répondent pas
                dispatcher.utter_message(text=PAS_TROUVE)
                return []
            if utilises is None:                     # sources non indiquées : on les cite toutes
                utilises = list(range(1, len(resultats) + 1))
            sources = " ; ".join(dict.fromkeys(resultats[i - 1][0][0] for i in utilises))
            dispatcher.utter_message(text=f"{texte}\n\nSource : {sources}")
            return []
 
        # LLM indisponible : on n'affiche des extraits bruts que s'ils sont réellement proches
        proches = [r for r in resultats if r[1] >= SEUIL][:2]
        if not proches:
            dispatcher.utter_message(text=PAS_TROUVE)
            return []
        morceaux = "\n\n".join(f"{texte}\n(Source : {fichier})" for (fichier, texte), _ in proches)
        dispatcher.utter_message(text="D'après la documentation :\n\n" + morceaux)
        return []


class ValidateSuiviForm(FormValidationAction):

    def name(self) -> Text: 
        return "validate_suivi_form"

    def validate_navire(self, slot_value, dispatcher, tracker, domain):
        nom = _normaliser(slot_value)
        if db.navire(nom):
            return {"navire": nom}
        liste = "\n".join(f"- {n.capitalize()}" for n in db.noms_navires())
        dispatcher.utter_message(
            text="Je ne trouve pas ce navire. Navires suivis :\n" + liste)
        return {"navire": None}
    
class ActionSuiviNavire(Action):

    def name(self) -> Text:
        return "action_suivi_navire"

    def run(self, dispatcher, tracker, domain):
        nom = tracker.get_slot("navire")
        info = db.navire(nom) if nom else None
        if not info:
            dispatcher.utter_message(text="Je ne retrouve plus ce navire, pouvez-vous répéter son nom ?")
            return [SlotSet("navire", None)]
        dispatcher.utter_message(
            text=f"Navire {nom.title()} : {info['statut']}.\n"
                 f"- Poste : {info['poste']}\n"
                 f"- Prévision : {info['eta']}\n"
                 f"- Cargaison : {info['cargaison']}")
        return []

class ActionResetSuivi(Action):

    def name(self) -> Text:
        return "action_reset_suivi"

    def run(self, dispatcher, tracker, domain):
        return [SlotSet("navire", None)]
            
        

class ValidateRdvForm(FormValidationAction):
    def name(self)->Text:
        return "validate_rdv_form"

    def validate_nom(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker, 
        domain: DomainDict
    ) -> Dict[Text, Any]:
        nom = str(slot_value).strip()
        if len(nom) < 2:
            dispatcher.utter_message(text="Ce nom semble trop court.")
            return {"nom":None}
        return {"nom":nom.title()}

    def validate_email(
        self, 
        slot_value: Any, 
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict
    ) -> Dict[Text, Any]:
        email = str(slot_value).strip().lower()
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return {"email": email}
        dispatcher.utter_message(text="Cette adresse email ne semble pas valide.")
        return {"email":None}

    def validate_motif(
        self, 
        slot_value: Any, 
        dispatcher: CollectingDispatcher, 
        tracker: Tracker, 
        domain: DomainDict
    )-> Dict[Text, Any]:
        motif = str(slot_value).strip()
        if len(motif) < 3:
            dispatcher.utter_message(text="Vous devez entrer un motif.")
            return {"motif": None}
        return {"motif": motif}

class ActionEnregistrerRdv(Action):

    def name(self) -> Text:
        return "action_enregistrer_rdv"

    def run(
        self, 
        dispatcher, 
        tracker, 
        domain
        ):
        try:
            db.enregistrer_rdv(tracker.get_slot("nom"), tracker.get_slot("email"), tracker.get_slot("motif"))
        except Exception as e:
            print(f"[DB] enregistrement du rendez-vous impossible : {str(e)[:200]}")
            dispatcher.utter_message(
                text="Je n'ai pas pu enregistrer votre demande pour le moment. Merci de réessayer plus tard.")
        return []

class ActionResetRdvSlots(Action):
    
    def name(self) -> Text:
        return "action_reset_rdv_slots"

    def run(self, dispatcher, tracker, domain):
        return [SlotSet("nom", None), SlotSet('email', None), SlotSet('motif', None)]

class ActionDoonerHoraires(Action):

    def name(self)->Text: 
        return "action_donner_horaires"

    def run(
        self, 
        dispatcher: CollectingDispatcher, 
        tracker: Tracker, 
        domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:

        jour = tracker.get_slot("jour")

        if jour is None: 
            dispatcher.utter_message(
                text="Nous sommes ouverts du lundi au vendredi, de 8h à 17h. "
                     "Nous sommes fermés le week-end."
            )

            return []
        
        jour = jour.lower().strip()

        if jour not in HORAIRES:
            dispatcher.utter_message(
                text=f"Je ne connais pas le jour « {jour} ». "
                     "Pouvez-vous préciser (lundi, mardi, etc.) ?"
            )
            return []

        horaire = HORAIRES[jour]
        if horaire is None:
            dispatcher.utter_message(text=f"Le {jour}, nous sommes fermés.")
        else:
            dispatcher.utter_message(
                text=f"Le {jour}, nous sommes ouverts {horaire}."
            )
        return []

        