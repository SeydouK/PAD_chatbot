"""Crée ou remplace un compte admin :  python -m admin_api.create_admin NOM"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from actions import store  # noqa: E402

if len(sys.argv) != 2:
    sys.exit("Usage : python -m admin_api.create_admin NOM")
mdp = getpass.getpass("Mot de passe (10 caractères minimum) : ")
if mdp != getpass.getpass("Confirmez : "):
    sys.exit("Les mots de passe diffèrent.")
try:
    store.creer_admin(sys.argv[1].strip().lower(), mdp)
except ValueError as e:
    sys.exit(str(e))
print("Compte enregistré.")
