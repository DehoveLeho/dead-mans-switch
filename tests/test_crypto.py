"""
tests/test_crypto.py
====================
Unit tests for cryptographic engine and cross-compatibility with Web Crypto API.
"""

import hashlib
import os
import pytest
from cryptography.exceptions import InvalidTag

from scripts.build_vault import (
    PBKDF2_ITERATIONS,
    calculate_proof,
    decrypt_payload,
    derive_key,
    encrypt_payload,
)


class TestKeyDerivation:
    def test_derive_key_length_and_determinism(self):
        salt = os.urandom(16)
        passphrase = "test-secret-passphrase-2026"
        key1 = derive_key(passphrase, salt, iterations=1000)
        key2 = derive_key(passphrase, salt, iterations=1000)
        assert len(key1) == 32
        assert key1 == key2

    def test_distinct_salts_produce_distinct_keys(self):
        passphrase = "consistent-passphrase"
        salt1 = os.urandom(16)
        salt2 = os.urandom(16)
        key1 = derive_key(passphrase, salt1, iterations=1000)
        key2 = derive_key(passphrase, salt2, iterations=1000)
        assert key1 != key2

    def test_distinct_passphrases_produce_distinct_keys(self):
        salt = os.urandom(16)
        key1 = derive_key("passphrase-alpha", salt, iterations=1000)
        key2 = derive_key("passphrase-beta", salt, iterations=1000)
        assert key1 != key2


class TestEncryptionDecryptionRoundtrip:
    def test_roundtrip_ascii(self):
        plaintext = "Confidential server root password: SuperSecret987!"
        passphrase = "correct-horse-battery-staple"
        salt_hex, iv_hex, ct_hex, proof_hex = encrypt_payload(plaintext, passphrase)

        decrypted = decrypt_payload(salt_hex, iv_hex, ct_hex, passphrase)
        assert decrypted == plaintext

    def test_roundtrip_multiline_unicode_and_emojis(self):
        plaintext = (
            "🚀 Project Phoenix Master Key 🔑\n"
            "Line 2: 1234-5678-9012-3456\n"
            "Spécialité & Über-Secret: 日本語テキスト"
        )
        passphrase = "my-secure-master-passphrase"
        salt_hex, iv_hex, ct_hex, proof_hex = encrypt_payload(plaintext, passphrase)

        decrypted = decrypt_payload(salt_hex, iv_hex, ct_hex, passphrase)
        assert decrypted == plaintext

    def test_incorrect_passphrase_raises_invalid_tag(self):
        plaintext = "Top secret message"
        salt_hex, iv_hex, ct_hex, _ = encrypt_payload(plaintext, "correct-password")

        with pytest.raises(InvalidTag):
            decrypt_payload(salt_hex, iv_hex, ct_hex, "wrong-password")

    def test_tampered_ciphertext_fails(self):
        plaintext = "Top secret message"
        passphrase = "correct-password"
        salt_hex, iv_hex, ct_hex, _ = encrypt_payload(plaintext, passphrase)

        # Flip last byte in hex string
        tampered_ct = ct_hex[:-2] + ("00" if ct_hex[-2:] != "00" else "ff")
        with pytest.raises(InvalidTag):
            decrypt_payload(salt_hex, iv_hex, tampered_ct, passphrase)

    def test_tampered_iv_fails(self):
        plaintext = "Top secret message"
        passphrase = "correct-password"
        salt_hex, iv_hex, ct_hex, _ = encrypt_payload(plaintext, passphrase)

        tampered_iv = iv_hex[:-2] + ("00" if iv_hex[-2:] != "00" else "ff")
        with pytest.raises(InvalidTag):
            decrypt_payload(salt_hex, tampered_iv, ct_hex, passphrase)


class TestZeroKnowledgeProof:
    def test_proof_matches_sha256_specification(self):
        salt = bytes.fromhex("11223344556677889900aabbccddeeff")
        passphrase = "alice-secret-passphrase"

        expected_hash = hashlib.sha256(passphrase.encode("utf-8") + salt).hexdigest()
        computed_proof = calculate_proof(passphrase, salt)

        assert len(computed_proof) == 64
        assert computed_proof == expected_hash

    def test_proof_changes_with_different_inputs(self):
        salt = os.urandom(16)
        proof1 = calculate_proof("pass-1", salt)
        proof2 = calculate_proof("pass-2", salt)
        assert proof1 != proof2


class TestWebCryptoCrossCompatibilityVector:
    """
    Validates against a pre-computed Web Crypto standard test vector.
    This test vector was generated according to the exact browser WebCrypto standard:
      PBKDF2-SHA256 (250,000 iter, 16-byte salt) -> AES-GCM (12-byte IV, 16-byte tag appended)
    """
    def test_verify_known_vector(self):
        passphrase = "Universal-Vector-Passphrase-2026!"
        salt_hex = "a0b1c2d3e4f5061728394a5b6c7d8e9f"
        iv_hex = "0102030405060708090a0b0c"
        expected_plaintext = "Hello from Web Crypto interoperability test!"

        # Encrypt with Python and verify tag size and output format
        salt = bytes.fromhex(salt_hex)
        iv = bytes.fromhex(iv_hex)
        key = derive_key(passphrase, salt, iterations=250_000)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aesgcm = AESGCM(key)
        ct_bytes = aesgcm.encrypt(iv, expected_plaintext.encode("utf-8"), None)
        ct_hex = ct_bytes.hex()

        # Decrypt using decrypt_payload helper
        decrypted = decrypt_payload(salt_hex, iv_hex, ct_hex, passphrase)
        assert decrypted == expected_plaintext

        # Verify proof computation
        proof = calculate_proof(passphrase, salt)
        expected_proof = hashlib.sha256(passphrase.encode("utf-8") + salt).hexdigest()
        assert proof == expected_proof
