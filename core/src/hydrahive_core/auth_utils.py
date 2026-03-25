"""
auth_utils.py — Passwort-Hashing Utilities

Ausgelagert aus main.py damit die Funktionen testbar sind
ohne den kompletten FastAPI-Stack zu importieren.
"""
import hashlib
import secrets


def hash_password(password: str) -> str:
    """
    PBKDF2-SHA256 mit korrekter Binär-Salt (pbkdf2b-Format).
    Altes pbkdf2-Format wird beim Verify noch unterstützt (Rückwärtskompatibilität).
    """
    salt_bytes = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt_bytes, 260_000)
    return f"pbkdf2b:{salt_bytes.hex()}:{h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """
    Verifiziert ein Passwort gegen einen gespeicherten Hash.
    Unterstützt pbkdf2b (korrekt) und pbkdf2 (Legacy) Format.
    """
    try:
        scheme, salt_str, h = stored.split(":", 2)
        if scheme == "pbkdf2b":
            # Korrekt: salt als echte Bytes
            salt = bytes.fromhex(salt_str)
        else:
            # Legacy pbkdf2: salt war ASCII-kodierter Hex-String
            salt = salt_str.encode()
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
        return check.hex() == h
    except Exception:
        return False
