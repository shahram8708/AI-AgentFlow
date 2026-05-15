"""Credential vault encryption service using AES 256 GCM."""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from flask import current_app, has_app_context


class EncryptionError(Exception):
    """Raised when encryption operations fail."""


class DecryptionError(Exception):
    """Raised when decryption operations fail."""


class EncryptionService:
    """Encrypt and decrypt vault values using AES 256 GCM."""

    DEFAULT_KEY_REF: Final[str] = "v1_pbkdf2_sha256"
    NONCE_LENGTH: Final[int] = 12
    TAG_LENGTH: Final[int] = 16
    KEY_LENGTH: Final[int] = 32
    PBKDF2_ITERATIONS: Final[int] = 100_000

    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}

    def _read_secret_key(self) -> str:
        secret_key = ""
        if has_app_context():
            secret_key = str(current_app.config.get("SECRET_KEY") or "")
        if not secret_key:
            secret_key = str(os.environ.get("SECRET_KEY") or "")

        if len(secret_key) < 32:
            raise EncryptionError(
                "SECRET_KEY too short for secure encryption - minimum 32 characters required"
            )

        return secret_key

    @staticmethod
    def _derive_salt(key_ref: str) -> bytes:
        app_name = "agentflow"
        app_domain = "agentflow.ai"

        if has_app_context():
            app_name = str(current_app.config.get("APP_NAME") or app_name)
            app_domain = str(
                current_app.config.get("SERVER_NAME")
                or current_app.config.get("FRONTEND_URL")
                or app_domain
            )

        seed = f"{app_name}|{app_domain}|{key_ref}".encode("utf-8")
        return hashlib.sha256(seed).digest()[:16]

    def _derive_key(self, key_ref: str) -> bytes:
        if key_ref in self._keys:
            return self._keys[key_ref]

        secret_key = self._read_secret_key()
        salt = self._derive_salt(key_ref)
        try:
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=self.KEY_LENGTH,
                salt=salt,
                iterations=self.PBKDF2_ITERATIONS,
            )
            key = kdf.derive(secret_key.encode("utf-8"))
        except Exception as exc:  # pylint: disable=broad-except
            raise EncryptionError("Unable to derive encryption key") from exc

        self._keys[key_ref] = key
        return key

    def _encrypt_with_ref(self, plaintext: str, key_ref: str) -> str:
        if not isinstance(plaintext, str):
            raise EncryptionError("Credential value must be a string")

        key = self._derive_key(key_ref)

        try:
            nonce = os.urandom(self.NONCE_LENGTH)
            aesgcm = AESGCM(key)
            ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
            encrypted_blob = nonce + ciphertext_with_tag
            encrypted_value = base64.urlsafe_b64encode(encrypted_blob).decode("utf-8")

            if has_app_context():
                current_app.logger.debug(
                    "Vault credential encrypted with key ref %s token_prefix=%s",
                    key_ref,
                    encrypted_value[:8],
                )

            return encrypted_value
        except EncryptionError:
            raise
        except Exception as exc:  # pylint: disable=broad-except
            raise EncryptionError("Unable to encrypt credential value") from exc

    def _decrypt_with_ref(self, encrypted_value: str, key_ref: str) -> str:
        key = self._derive_key(key_ref)

        try:
            raw = base64.urlsafe_b64decode(encrypted_value.encode("utf-8"))
        except Exception as exc:  # pylint: disable=broad-except
            raise DecryptionError("Invalid encrypted payload format") from exc

        if len(raw) <= self.NONCE_LENGTH + self.TAG_LENGTH:
            raise DecryptionError("Invalid encrypted payload format")

        nonce = raw[: self.NONCE_LENGTH]
        ciphertext = raw[self.NONCE_LENGTH : -self.TAG_LENGTH]
        tag = raw[-self.TAG_LENGTH :]

        try:
            aesgcm = AESGCM(key)
            plaintext_bytes = aesgcm.decrypt(nonce, ciphertext + tag, None)
            return plaintext_bytes.decode("utf-8")
        except InvalidTag as exc:
            raise DecryptionError("Credential integrity verification failed") from exc
        except DecryptionError:
            raise
        except Exception as exc:  # pylint: disable=broad-except
            raise DecryptionError("Unable to decrypt credential value") from exc

    def encrypt(self, plaintext: str) -> tuple[str, str]:
        """Encrypt plaintext and return value with key reference."""

        key_ref = self.DEFAULT_KEY_REF
        encrypted_value = self._encrypt_with_ref(plaintext, key_ref)
        return encrypted_value, key_ref

    def decrypt(self, encrypted_value: str, encryption_key_ref: str) -> str:
        """Decrypt encrypted value using the provided key reference."""

        if not encrypted_value:
            raise DecryptionError("Encrypted value is required")

        key_ref = (encryption_key_ref or "").strip() or self.DEFAULT_KEY_REF
        return self._decrypt_with_ref(encrypted_value, key_ref)

    def verify_integrity(self, encrypted_value: str, encryption_key_ref: str) -> bool:
        """Return True when encrypted payload passes authentication checks."""

        try:
            self.decrypt(encrypted_value, encryption_key_ref)
            return True
        except (EncryptionError, DecryptionError):
            return False

    def rotate_key(
        self,
        old_encrypted_value: str,
        old_key_ref: str,
        new_key_ref: str,
    ) -> str:
        """Decrypt with old key reference and re encrypt with new reference."""

        previous_key_ref = (old_key_ref or "").strip() or self.DEFAULT_KEY_REF
        target_key_ref = (new_key_ref or "").strip()
        if not target_key_ref:
            raise EncryptionError("New key reference is required")

        plaintext = self._decrypt_with_ref(old_encrypted_value, previous_key_ref)
        return self._encrypt_with_ref(plaintext, target_key_ref)


encryption_service = EncryptionService()


def encrypt_value(plaintext: str) -> str:
    """Backward compatible helper that returns encrypted token only."""

    encrypted_value, _key_ref = encryption_service.encrypt(plaintext)
    return encrypted_value


def decrypt_value(token: str) -> str:
    """Backward compatible helper using the default key reference."""

    return encryption_service.decrypt(token, EncryptionService.DEFAULT_KEY_REF)
