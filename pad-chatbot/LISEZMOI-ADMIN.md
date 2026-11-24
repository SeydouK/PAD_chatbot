# Interface d'administration du chatbot PAD

Trois choses : se connecter (compte propre au chatbot), ajouter/supprimer des documents, configurer les bases de données.

## 1. Fichiers
Copiez le contenu du zip dans `pad-chatbot` en conservant les dossiers :
- `actions/` : `db.py`, `store.py` (nouveau), `question_donnees.py`, `actions.py`, `rag.py`, `llm.py` (remplacent les anciens)
- `admin_api/` (nouveau), `export_db.py` (remplace l'ancien), `export_db.yml`, `requirements-admin.txt`
- `pad-chat-web/` : copiez `src/app/` et `proxy.conf.json`. **Supprimez** `src/app/app.html` et `src/app/app.scss` (remplacés).

## 2. Installer et lancer l'API d'administration
Dans un environnement virtuel SÉPARÉ de celui de Rasa (évite les conflits de versions) :
```
python -m venv .venv-admin
.venv-admin\Scripts\activate
pip install -r requirements-admin.txt
python -m admin_api.create_admin seydou        (demande le mot de passe, 10 caractères minimum)
python -m admin_api                            (écoute sur http://127.0.0.1:8010)
```
Puis `npm start` dans `pad-chat-web` : l'interface est sur http://localhost:4200/admin
(le proxy envoie `/admin-api` vers le port 8010).

## 3. Utilisation
- **Documents** : téléversez PDF, Word, .txt ou .md (10 Mo max). Le texte est extrait, enregistré dans `docs/uploads/`, et le chatbot l'utilise dès la question suivante. Les PDF scannés (images) sont refusés : il faudrait un OCR.
- **Bases de données** : ajoutez une connexion (PostgreSQL, MySQL ou SQLite), cliquez « Tester la connexion », enregistrez.
  - Case « base principale » : le bot (suivi des navires, rendez-vous) utilise cette base. `DATABASE_URL`, si elle est définie, reste prioritaire.
  - « Tables à exporter » : choisissez une table, la colonne identifiant, les colonnes de texte et un filtre éventuel, puis « Enregistrer et lancer l'export ». Les textes arrivent dans `docs/db/`.
  - Pour relancer les exports automatiquement (planificateur Windows) : `python export_db.py --admin`
- Mot de passe oublié : relancez `python -m admin_api.create_admin NOM` (remplace le mot de passe).

## 4. Sécurité : à lire
- Les mots de passe des bases sont chiffrés dans `admin.db` avec la clé du fichier `.admin_secret` (ou la variable `PAD_SECRET`). **Sauvegardez les deux séparément, ne les mettez pas dans Git** (ajoutez `admin.db`, `.admin_secret`, `pad.db` au `.gitignore`). Sans la clé, les mots de passe enregistrés sont perdus.
- L'API n'écoute que sur 127.0.0.1. Pour l'ouvrir au réseau, placez-la derrière un proxy HTTPS : sans HTTPS, mots de passe et jetons circulent en clair.
- Connexion : 5 échecs = blocage de 5 minutes ; session de 8 h ; jeton gardé dans l'onglet seulement.
- Utilisez pour les exports un compte SQL en lecture seule. Tout ce qui est exporté est consultable par tous les visiteurs du chatbot.
- Pas encore de rôles (tous les admins ont les mêmes droits) ni de journal des actions.

## 5. Ce qui n'a pas été testé
MySQL et PostgreSQL réels (essais faits en SQLite), SQLAlchemy 1.4 (utilisé par Rasa ; essais en 2.0), et l'API derrière un vrai proxy HTTPS.
