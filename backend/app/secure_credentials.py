## @file secure_credentials.py
#  @brief Encrypt/decrypt the BDD3 connection credentials for temporary
#  deployments where a plain .env file is not desired (e.g. a folder copied
#  to a maintainer's PC via USB drive). Uses Fernet symmetric encryption
#  (AES128 under the hood) from the standard "cryptography" library - no
#  custom cryptography.
#
#  This protects against someone opening the credentials file directly
#  (they only see unreadable ciphertext) and against the file being
#  accidentally committed to Git (useless without the separate key file).
#  It does NOT protect against someone with access to both this file, the
#  key file, and the source code - that level of protection requires a
#  real secrets manager or per-user authentication, which is a separate,
#  larger topic (see the project README).

import json
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken

## Default path for the encrypted credentials file.
DEFAULT_CREDENTIALS_FILE = Path(__file__).resolve().parent.parent / "credentials.enc.json"
## Default path for the key file needed to decrypt it.
DEFAULT_KEY_FILE = Path(__file__).resolve().parent.parent / "credentials.key"


def generate_key() -> bytes:
    """!
    @brief Generate a new random Fernet encryption key.

    @return A URL-safe base64-encoded 32-byte key, suitable for
    Fernet(key).
    """
    return Fernet.generate_key()


def encrypt_credentials(credentials: dict, key: bytes, output_file: Path = DEFAULT_CREDENTIALS_FILE) -> None:
    """!
    @brief Encrypt a dict of database credentials and write it to a JSON
    file as a single ciphertext blob.

    @param credentials Dict with keys such as DB_HOST, DB_PORT, DB_NAME,
    DB_USER, DB_PASSWORD.
    @param key Fernet key, as returned by generate_key().
    @param output_file Path to write the encrypted JSON file to.
    """
    fernet = Fernet(key)
    plaintext = json.dumps(credentials).encode("utf-8")
    ciphertext = fernet.encrypt(plaintext)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({"ciphertext": ciphertext.decode("utf-8")}, f, indent=2)


def decrypt_credentials(key: bytes, input_file: Path = DEFAULT_CREDENTIALS_FILE) -> dict:
    """!
    @brief Read and decrypt a credentials file produced by
    encrypt_credentials.

    @param key Fernet key matching the one used to encrypt the file.
    @param input_file Path to the encrypted JSON file.
    @return Dict of decrypted credentials.
    @exception InvalidToken If the key does not match the one used to
    encrypt the file, or the file has been tampered with.
    """
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    fernet = Fernet(key)
    plaintext = fernet.decrypt(data["ciphertext"].encode("utf-8"))
    return json.loads(plaintext.decode("utf-8"))


def load_key(key_file: Path = DEFAULT_KEY_FILE) -> bytes:
    """!
    @brief Read a Fernet key from a file.

    @param key_file Path to the key file.
    @return The key as bytes.
    """
    with open(key_file, "rb") as f:
        return f.read().strip()


def credentials_files_available(
    credentials_file: Path = DEFAULT_CREDENTIALS_FILE,
    key_file: Path = DEFAULT_KEY_FILE,
) -> bool:
    """!
    @brief Check whether both the encrypted credentials file and the key
    file are present.

    @param credentials_file Path to the encrypted JSON file.
    @param key_file Path to the key file.
    @return True if both files exist.
    """
    return credentials_file.exists() and key_file.exists()
