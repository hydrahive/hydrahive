"""
secret_encryption.py — Fernet Encryption für Secrets at Rest (#356)

Verschlüsselt/entschlüsselt Secrets mit einem Master-Key der
beim ersten Start generiert und in /etc/hydrahive/master.key gespeichert wird.

Verwendung:
    from .secret_encryption import encrypt_secret, decrypt_secret
    encrypted = encrypt_secret("mein-geheimnis")
    original = decrypt_secret(encrypted)
"""
import logging
from pathlib import Path

from cryptography.fernet import Fernet

from .settings import settings

logger = logging.getLogger(__name__)

_MASTER_KEY_FILE = settings.etc_dir / "master.key"
_fernet: Fernet | None = None


def _load_or_create_master_key() -> bytes:
    """Master-Key laden oder beim ersten Start generieren."""
    if _MASTER_KEY_FILE.exists():
        return _MASTER_KEY_FILE.read_bytes().strip()
    key = Fernet.generate_key()
    _MASTER_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _MASTER_KEY_FILE.write_bytes(key)
    _MASTER_KEY_FILE.chmod(0o600)
    logger.info("Neuer Master-Key generiert: %s", _MASTER_KEY_FILE)
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_master_key())
    return _fernet


def encrypt_secret(plaintext: str) -> str:
    """Verschlüsselt einen String und gibt den Fernet-Token zurück."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    """Entschlüsselt einen Fernet-Token zurück zum Original-String."""
    return _get_fernet().decrypt(token.encode()).decode()


def is_encrypted(value: str) -> bool:
    """Prüft ob ein Wert ein Fernet-Token ist (beginnt mit 'gAAAAA')."""
    return value.startswith("gAAAAA") and len(value) > 50
