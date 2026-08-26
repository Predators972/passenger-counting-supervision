## @file generate_credentials.py
#  @brief Reads the BDD3 connection details from .env, encrypts them, and
#  writes credentials.enc.json and credentials.key.
#
#  Run from backend/, virtual environment activated:
#      python generate_credentials.py

import sys
from pathlib import Path
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.secure_credentials import generate_key, encrypt_credentials, DEFAULT_CREDENTIALS_FILE, DEFAULT_KEY_FILE

ENV_FILE = Path(__file__).resolve().parent / ".env"


def main():
    if not ENV_FILE.exists():
        print(f"Erreur : {ENV_FILE} introuvable.")
        sys.exit(1)

    env_values = dotenv_values(ENV_FILE)
    credentials = {
        "DB_HOST": env_values.get("DB_HOST"),
        "DB_PORT": env_values.get("DB_PORT", "5432"),
        "DB_NAME": env_values.get("DB_NAME"),
        "DB_USER": env_values.get("DB_USER"),
        "DB_PASSWORD": env_values.get("DB_PASSWORD"),
    }

    missing = [k for k, v in credentials.items() if not v]
    if missing:
        print(f"Erreur : champs manquants dans .env : {', '.join(missing)}")
        sys.exit(1)

    key = generate_key()
    encrypt_credentials(credentials, key, DEFAULT_CREDENTIALS_FILE)

    with open(DEFAULT_KEY_FILE, "wb") as f:
        f.write(key)

    print(f"Fichier chiffré écrit : {DEFAULT_CREDENTIALS_FILE}")
    print(f"Clé de déchiffrement écrite : {DEFAULT_KEY_FILE}")


if __name__ == "__main__":
    main()
