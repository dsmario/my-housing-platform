"""Doc Vault keys: per-youth and per-document keys, file encryption, crypto-shredding."""

import uuid

import boto3
import pytest
from botocore.stub import Stubber

import vault_keys as vk
from identity import Principal
from people import current_youth


def new_youth() -> str:
    p = Principal(subject=f"test-sub-{uuid.uuid4()}", username="y", groups=("youth",), pool="users")
    return current_youth(p).pseudonym_id


def doc_id() -> str:
    return str(uuid.uuid4())


def test_test_key_is_32_bytes():
    assert len(vk.get_provider().key) == 32


def test_youth_key_is_made_once_and_stored_wrapped(conn):
    youth = new_youth()
    first = vk.youth_data_key(conn, youth)
    again = vk.youth_data_key(conn, youth)
    assert first == again and len(first) == 32
    wrapped, provider, key_id = conn.execute(
        "SELECT wrapped_key, key_provider, wrapping_key_id FROM vault.youth_data_key WHERE youth_pseudonym_id = %s",
        (youth,),
    ).fetchone()
    assert (provider, key_id) == ("local", "local")
    assert first not in bytes(wrapped)


def test_each_youth_gets_their_own_key(conn):
    assert vk.youth_data_key(conn, new_youth()) != vk.youth_data_key(conn, new_youth())


def test_a_wrapped_key_copied_to_another_youth_does_not_open(conn):
    a, b = new_youth(), new_youth()
    vk.youth_data_key(conn, a)
    wrapped = conn.execute(
        "SELECT wrapped_key FROM vault.youth_data_key WHERE youth_pseudonym_id = %s", (a,)
    ).fetchone()[0]
    with pytest.raises(vk.DecryptFailed):
        vk.get_provider().unwrap(bytes(wrapped), b)


def test_file_round_trip():
    youth_key = bytes(32)
    d = doc_id()
    key, wrapped = vk.new_document_key(youth_key, d)
    assert key not in wrapped
    blob = vk.encrypt_file(key, b"%PDF-1.4 hello", d)
    assert b"hello" not in blob and blob.startswith(vk.FILE_HEADER)
    assert vk.decrypt_file(vk.unwrap_document_key(youth_key, wrapped, d), blob, d) == b"%PDF-1.4 hello"


def test_changed_file_is_refused():
    d = doc_id()
    key, _ = vk.new_document_key(bytes(32), d)
    blob = bytearray(vk.encrypt_file(key, b"some document", d))
    blob[-1] ^= 1
    with pytest.raises(vk.DecryptFailed):
        vk.decrypt_file(key, bytes(blob), d)


def test_file_swapped_between_documents_is_refused():
    d1, d2 = doc_id(), doc_id()
    key, _ = vk.new_document_key(bytes(32), d1)
    with pytest.raises(vk.DecryptFailed):
        vk.decrypt_file(key, vk.encrypt_file(key, b"x", d1), d2)


def test_document_key_needs_the_owners_key():
    d = doc_id()
    _, wrapped = vk.new_document_key(b"a" * 32, d)
    with pytest.raises(vk.DecryptFailed):
        vk.unwrap_document_key(b"b" * 32, wrapped, d)


def test_destroyed_youth_key_makes_documents_unreadable(conn):
    youth = new_youth()
    youth_key = vk.youth_data_key(conn, youth)
    d = doc_id()
    key, wrapped = vk.new_document_key(youth_key, d)
    blob = vk.encrypt_file(key, b"private", d)

    vk.destroy_youth_key(conn, youth)
    with pytest.raises(vk.KeyDestroyed):
        vk.youth_data_key(conn, youth)
    # The stored file and wrapped document key remain, but nothing can open them.
    assert blob and wrapped
    destroyed = conn.execute(
        "SELECT wrapped_key IS NULL, destroyed_at IS NOT NULL FROM vault.youth_data_key WHERE youth_pseudonym_id = %s",
        (youth,),
    ).fetchone()
    assert destroyed == (True, True)


def test_key_made_by_another_provider_is_not_mixed_up(conn):
    youth = new_youth()
    vk.youth_data_key(conn, youth)

    class OtherProvider:
        name, key_id = "kms", "arn:aws:kms:us-west-2:000000000000:key/test"

    with pytest.raises(RuntimeError, match="wrapped by 'local'"):
        vk.youth_data_key(conn, youth, OtherProvider())


@pytest.mark.parametrize("value", ["", "c2hvcnQ=", "not base64!"])
def test_bad_local_key_is_refused(value):
    with pytest.raises((RuntimeError, ValueError)):
        vk.LocalProvider(value)


# KMS provider, against a stubbed client (never calls AWS) --------------------

KEY_ARN = "arn:aws:kms:us-west-2:000000000000:key/test"


def kms():
    client = boto3.client("kms", region_name="us-west-2", aws_access_key_id="x", aws_secret_access_key="x")
    return client, Stubber(client)


def test_kms_generate_and_unwrap_bind_the_youth():
    client, stub = kms()
    youth = str(uuid.uuid4())
    context = {"youth_pseudonym_id": youth}
    stub.add_response(
        "generate_data_key",
        {"Plaintext": b"k" * 32, "CiphertextBlob": b"wrapped", "KeyId": KEY_ARN},
        {"KeyId": KEY_ARN, "KeySpec": "AES_256", "EncryptionContext": context},
    )
    stub.add_response(
        "decrypt",
        {"Plaintext": b"k" * 32, "KeyId": KEY_ARN},
        {"CiphertextBlob": b"wrapped", "KeyId": KEY_ARN, "EncryptionContext": context},
    )
    with stub:
        p = vk.KmsProvider(KEY_ARN, client)
        assert p.generate(youth) == (b"k" * 32, b"wrapped")
        assert p.unwrap(b"wrapped", youth) == b"k" * 32
    stub.assert_no_pending_responses()


def test_kms_refusal_is_a_decrypt_failure():
    client, stub = kms()
    stub.add_client_error("decrypt", service_error_code="InvalidCiphertextException")
    with stub, pytest.raises(vk.DecryptFailed):
        vk.KmsProvider(KEY_ARN, client).unwrap(b"wrapped", str(uuid.uuid4()))


def test_unknown_provider_setting_is_refused(monkeypatch):
    monkeypatch.setenv("VAULT_KEY_PROVIDER", "plaintext")
    vk.get_provider.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="VAULT_KEY_PROVIDER"):
            vk.get_provider()
    finally:
        monkeypatch.setenv("VAULT_KEY_PROVIDER", "local")
        vk.get_provider.cache_clear()
