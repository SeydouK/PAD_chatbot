# Utilisateurs, groupes et SQL Server — guide simple

## 1. Ce que ça fait (exemple)
Vous créez le groupe **RH** et l'utilisateur **awa** (dans RH). Vous marquez le document `salaires.pdf` « visible par RH ».
- Un visiteur NON connecté qui demande « quels sont les salaires ? » → le bot ne voit PAS ce document.
- **awa**, connectée dans le chat → le bot l'utilise et le cite.
- Un document sans groupe reste **public** (comme avant).

## 2. Mise en place (3 commandes)
1. Copiez `actions/`, `admin_api/`, `export_db.py`, `requirements-admin.txt` dans `pad-chatbot` (remplacez).
2. Dans `.venv-admin` : `pip install -r requirements-admin.txt` (ajoute `pymssql`).
3. Relancez `python -m admin_api` ET `rasa run actions` (les deux lisent le même `admin.db`, sur la même machine).
Angular : copiez tout `src/app` (dossier `admin/` inclus) puis `npm start`.

## 3. Utilisation
- Admin → **Utilisateurs** : créer des groupes, créer des utilisateurs (cochez leurs groupes), changer un mot de passe, désactiver, supprimer.
- Admin → **Documents** → colonne « Visible par » → **Modifier** → cochez les groupes → Enregistrer. Rien coché = public. Marche pour tous les documents (téléversés, site, base, API).
- Chat : icône « personne » en haut du widget → identifiant + mot de passe. Les changements (retrait d'un groupe, désactivation) agissent tout de suite.

## 4. SQL Server
Admin → Bases de données → type **SQL Server** (port 1433). Utilisable pour l'export de tables ET comme base principale (navires, rendez-vous : tables créées automatiquement en T-SQL).
- SQL Server Express : activez TCP/IP dans « SQL Server Configuration Manager » et utilisez le port fixe (ou `hote` = `localhost`, port 1433).
- Pour la base principale côté Rasa : `pip install pymssql` dans l'environnement de `rasa run actions`.
- Pilote : `pymssql` (pur Python, sans ODBC). Alternative `pyodbc` possible mais demande « ODBC Driver 17/18 ».

## 5. Limites à connaître (honnêtement)
- **SQL Server non testé sur un vrai serveur** (seulement SQLite + vérification du code). Testez d'abord avec « Tester la connexion ».
- La sécurité repose sur l'identifiant de conversation (`u-…`, 256 bits aléatoires, créé par le serveur). Ne l'affichez ni ne le journalisez publiquement.
- Si vous supprimez un groupe, les documents qui n'avaient que lui redeviennent **publics** (l'interface vous prévient).
- Les réponses passées dans l'historique Rasa d'un utilisateur restent dans cet historique.
- Utilisez HTTPS en production (mots de passe dans les requêtes).
