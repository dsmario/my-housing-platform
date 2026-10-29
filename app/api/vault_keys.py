"""Doc Vault keys and file encryption (Doc Vault §4).

  youth data key     One per youth, made by KMS from the vault key. Stored only
                     wrapped (vault.youth_data_key), bound to the youth by the
                     KMS encryption context, so a wrapped key copied to another
                     youth won't unwrap. Destroying it makes all of that youth's
                     documents unreadable.
  document key       One per document, random. Wrapped with the youth's data key
                     (vault.document.wrapped_document_key). Removing it makes
                     that one document unreadable.
  file               AES-256-GCM with the document key. The document id is bound
                     in, so a stored file can't be swapped for another.

Which provider wraps youth keys is configuration (VAULT_KEY_PROVIDER):

  kms    AWS KMS. Needs VAULT_KMS_KEY_ID (the vault key's ARN or alias).
  local  Development only (§13). VAULT_LOCAL_KEY: 32 random bytes, base64.
"""

import base64
import os
from functools import lru_cache

import psycopg
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FILE_HEADER = b"MHPV1"
NONCE_BYTES = 12
KEY_BYTES = 32


class KeyDestroyed(Exception):
    """The youth's data key was destroyed; their documents can't be read."""


class DecryptFailed(Exception):
    """Wrong key, wrong owner, or the data was changed."""


def _seal(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def _open(key: bytes, sealed: bytes, aad: bytes) -> bytes:
    try:
        return AESGCM(key).decrypt(sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], aad)
    except (InvalidTag, ValueError) as e:
        raise DecryptFailed() from e


# Providers: wrap and unwrap youth data keys ---------------------------------


class KmsProvider:
    name = "kms"

    def __init__(self, key_id: str, client=None):
        if client is None:
            import boto3

            client = boto3.client("kms")
        self.key_id = key_id
        self.client = client

    def generate(self, youth_pseudonym_id: str) -> tuple[bytes, bytes]:
        r = self.client.generate_data_key(
            KeyId=self.key_id,
            KeySpec="AES_256",
            EncryptionContext={"youth_pseudonym_id": youth_pseudonym_id},
        )
        return r["Plaintext"], r["CiphertextBlob"]

    def unwrap(self, wrapped: bytes, youth_pseudonym_id: str) -> bytes:
        try:
            r = self.client.decrypt(
                CiphertextBlob=wrapped,
                KeyId=self.key_id,
                EncryptionContext={"youth_pseudonym_id": youth_pseudonym_id},
            )
        except self.client.exceptions.InvalidCiphertextException as e:
            raise DecryptFailed() from e
        return r["Plaintext"]


class LocalProvider:
    name = "local"
    key_id = "local"

    def __init__(self, key_b64: str):
        self.key = base64.b64decode(key_b64, validate=True)
        if len(self.key) != KEY_BYTES:
            raise RuntimeError("VAULT_LOCAL_KEY must be 32 bytes, base64 (openssl rand -base64 32)")

    def generate(self, youth_pseudonym_id: str) -> tuple[bytes, bytes]:
        plaintext = os.urandom(KEY_BYTES)
        return plaintext, _seal(self.key, plaintext, f"youth:{youth_pseudonym_id}".encode())

    def unwrap(self, wrapped: bytes, youth_pseudonym_id: str) -> bytes:
        return _open(self.key, wrapped, f"youth:{youth_pseudonym_id}".encode())


@lru_cache
def get_provider() -> KmsProvider | LocalProvider:
    provider = os.environ.get("VAULT_KEY_PROVIDER")
    if provider == "kms":
        return KmsProvider(os.environ["VAULT_KMS_KEY_ID"])
    if provider == "local":
        return LocalProvider(os.environ["VAULT_LOCAL_KEY"])
    raise RuntimeError(f"VAULT_KEY_PROVIDER must be 'kms' or 'local', got {provider!r}")


# Youth data keys --------------------------------------------------------------


def youth_data_key(conn: psycopg.Connection, youth_pseudonym_id: str, provider=None) -> bytes:
    """The youth's data key, unwrapped. Made on first use. The caller commits."""
    provider = provider or get_provider()
    row = conn.execute(
        "SELECT wrapped_key, key_provider FROM vault.youth_data_key WHERE youth_pseudonym_id = %s",
        (youth_pseudonym_id,),
    ).fetchone()
    if row is None:
        plaintext, wrapped = provider.generate(youth_pseudonym_id)
        inserted = conn.execute(
            "INSERT INTO vault.youth_data_key (youth_pseudonym_id, wrapped_key, key_provider, wrapping_key_id)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT (youth_pseudonym_id) DO NOTHING RETURNING id",
            (youth_pseudonym_id, wrapped, provider.name, provider.key_id),
        ).fetchone()
        if inserted is not None:
            return plaintext
        # Another request made it first; use theirs.
        return youth_data_key(conn, youth_pseudonym_id, provider)
    wrapped, key_provider = row
    if wrapped is None:
        raise KeyDestroyed()
    if key_provider != provider.name:
        raise RuntimeError(f"youth key was wrapped by {key_provider!r}, but {provider.name!r} is configured")
    return provider.unwrap(bytes(wrapped), youth_pseudonym_id)


def destroy_youth_key(conn: psycopg.Connection, youth_pseudonym_id: str) -> None:
    """Crypto-shredding for account closure (§4, §10). The caller commits."""
    conn.execute(
        "UPDATE vault.youth_data_key SET wrapped_key = NULL, destroyed_at = now()"
        " WHERE youth_pseudonym_id = %s AND wrapped_key IS NOT NULL",
        (youth_pseudonym_id,),
    )


# Document keys and files ------------------------------------------------------


def new_document_key(youth_key: bytes, document_id: str) -> tuple[bytes, bytes]:
    """Returns (document key, wrapped document key)."""
    key = os.urandom(KEY_BYTES)
    return key, _seal(youth_key, key, f"document:{document_id}".encode())


def unwrap_document_key(youth_key: bytes, wrapped: bytes, document_id: str) -> bytes:
    return _open(youth_key, bytes(wrapped), f"document:{document_id}".encode())


def encrypt_file(document_key: bytes, data: bytes, document_id: str) -> bytes:
    return FILE_HEADER + _seal(document_key, data, FILE_HEADER + document_id.encode())


def decrypt_file(document_key: bytes, blob: bytes, document_id: str) -> bytes:
    if not blob.startswith(FILE_HEADER):
        raise DecryptFailed()
    return _open(document_key, blob[len(FILE_HEADER) :], FILE_HEADER + document_id.encode())
