from __future__ import annotations
"""
Utilities per cifrare/decifrare token OAuth sensibili prima di salvarli nel DB.
Usa Fernet (AES-128-CBC + HMAC) da cryptography.
"""
import base64
import os
from app.config import get_settings
import structlog

logger = structlog.get_logger()


def _get_fernet():
    try:
        from cryptography.fernet import Fernet
        settings  = get_settings()
        # Deriva una chiave Fernet dalla SECRET_KEY
        key_bytes = settings.secret_key.encode()[:32].ljust(32, b'0')
        fernet_key = base64.urlsafe_b64encode(key_bytes)
        return Fernet(fernet_key)
    except ImportError:
        logger.warning("cryptography not installed — token encryption disabled")
        return None


def encrypt_token(plain: str) -> str:
    """Cifra un token OAuth. Ritorna la stringa cifrata base64."""
    f = _get_fernet()
    if not f:
        return plain
    return f.encrypt(plain.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """Decifra un token OAuth cifrato."""
    f = _get_fernet()
    if not f:
        return encrypted
    try:
        return f.decrypt(encrypted.encode()).decode()
    except Exception as e:
        logger.error("Token decryption failed", error=str(e))
        return ""
