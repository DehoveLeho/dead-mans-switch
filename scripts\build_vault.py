#!/usr/bin/env python3
"""
scripts/build_vault.py
======================
Autonomous Zero-Knowledge Dead Man's Switch - Offline Vault Generator CLI.
Génère le coffre-fort avec la D.A. et le format ✲ (deux niveaux de chiffrement).
"""

import argparse
import base64
import getpass
import hashlib
import json
import os
import sys
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS_1 = 100_000
ITERATIONS_2 = 250_000
PBKDF2_ITERATIONS = ITERATIONS_2
KEY_LENGTH = 32  # 256 bits


def encrypt_payload(plaintext: str, passphrase: str) -> Tuple[str, str, str, str]:
    """Chiffre un payload (compatibilité tests)."""
    salt = os.urandom(16)
    iv = os.urandom(12)
    key = derive_key(passphrase, salt, ITERATIONS_2)
    raw_ct = AESGCM(key).encrypt(iv, plaintext.encode("utf-8"), None)
    proof_hex = calculate_proof(passphrase, salt)
    return salt.hex(), iv.hex(), raw_ct.hex(), proof_hex


def decrypt_payload(salt_hex: str, iv_hex: str, ct_hex: str, passphrase: str) -> str:
    """Déchiffre un payload (compatibilité tests)."""
    salt = bytes.fromhex(salt_hex)
    iv = bytes.fromhex(iv_hex)
    raw_ct = bytes.fromhex(ct_hex)
    key = derive_key(passphrase, salt, ITERATIONS_2)
    dec = AESGCM(key).decrypt(iv, raw_ct, None)
    return dec.decode("utf-8")


def norm(s: str) -> str:
    """Normalise une chaîne (retire accents, minuscules, espaces)."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    ).lower().strip().replace("  ", " ")


def derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    """Dérive une clé AES 256-bit par PBKDF2-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def calculate_proof(passphrase: str, salt_bytes: bytes) -> str:
    """Calcule la preuve zéro-connaissance : SHA-256(passphrase + salt)."""
    hasher = hashlib.sha256()
    hasher.update(passphrase.encode("utf-8"))
    hasher.update(salt_bytes)
    return hasher.hexdigest()


def encrypt_entry(
    first_name: str,
    full_name: str,
    hint: str,
    message: str,
    passphrase: str,
    global_salt: str,
    recipient_id: str,
    email: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Chiffre une entrée à deux niveaux (Tier 1: nom/indice chiffré par prénom ; Tier 2: document chiffré par mot de passe).
    """
    norm_first = norm(first_name)
    h = hashlib.sha256((global_salt + norm_first).encode("utf-8")).hexdigest()

    # Tier 1 : Métadonnées chiffrées avec le prénom
    s1 = os.urandom(16)
    iv1 = os.urandom(12)
    key1 = derive_key(norm_first, s1, ITERATIONS_1)
    nb_payload = json.dumps({"name": full_name, "hint": hint, "id": recipient_id}, ensure_ascii=False).encode("utf-8")
    nb_ct = AESGCM(key1).encrypt(iv1, nb_payload, None)

    # Tier 2 : Document confidentiel chiffré avec la phrase secrète
    s2 = os.urandom(16)
    iv2 = os.urandom(12)
    key2 = derive_key(passphrase, s2, ITERATIONS_2)

    # Format de document compatible ✲ (title + blocks)
    doc_payload = json.dumps({
        "title": f"Pour {full_name}",
        "blocks": [{"t": "text", "v": message}]
    }, ensure_ascii=False).encode("utf-8")
    ct = AESGCM(key2).encrypt(iv2, doc_payload, None)

    proof_hex = calculate_proof(passphrase, s2)

    public_entry = {
        "id": recipient_id.strip().lower(),
        "name": full_name.strip(),
        "email": email.strip().lower(),
        "h": h,
        "s1": base64.b64encode(s1).decode("utf-8"),
        "iv1": base64.b64encode(iv1).decode("utf-8"),
        "nb": base64.b64encode(nb_ct).decode("utf-8"),
        "s2": base64.b64encode(s2).decode("utf-8"),
        "iv2": base64.b64encode(iv2).decode("utf-8"),
        "ct": base64.b64encode(ct).decode("utf-8"),
    }

    kv_entry = {
        "recipient_id": recipient_id.strip().lower(),
        "expected_proof": proof_hex,
        "email": email.strip().lower(),
        "name": full_name.strip(),
    }

    return public_entry, kv_entry


def process_config_file(config_path: str, global_salt: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_recipients = data if isinstance(data, list) else data.get("recipients", [])
    public_entries: List[Dict[str, Any]] = []
    kv_entries: List[Dict[str, Any]] = []

    for item in raw_recipients:
        rec_id = item["id"]
        full_name = item.get("name", rec_id)
        first_name = full_name.split()[0]
        email = item.get("email", "")
        hint = item.get("hint", "")
        message = item["message"]
        passphrase = item["passphrase"]

        pub, kv = encrypt_entry(first_name, full_name, hint, message, passphrase, global_salt, rec_id, email)
        public_entries.append(pub)
        kv_entries.append(kv)
        print(f"[+] Chiffré pour '{full_name}' (prénom: '{first_name}') -> hash: {pub['h'][:8]}...")

    return public_entries, kv_entries


def save_vault(
    public_entries: List[Dict[str, Any]],
    kv_entries: List[Dict[str, Any]],
    global_salt: str,
    output_vault_path: str = "portal/vault.json",
    output_kv_path: str = "worker/proofs_seed.json",
    worker_url: str = "",
) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(output_vault_path)), exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(output_kv_path)), exist_ok=True)

    # Schéma compatible ✲
    vault_data = {
        "salt": global_salt,
        "i1": ITERATIONS_1,
        "i2": ITERATIONS_2,
        "worker_url": worker_url,
        "e": public_entries,
        "recipients": [
            {"id": kv["recipient_id"], "name": kv["name"], "email": kv["email"]}
            for kv in kv_entries
        ],
    }

    with open(output_vault_path, "w", encoding="utf-8") as f:
        json.dump(vault_data, f, indent=2, ensure_ascii=False)
    print(f"\n[OK] Coffre-fort écrit dans : {output_vault_path}")

    # Données pour Cloudflare KV
    kv_bulk = []
    for kv in kv_entries:
        kv_bulk.append({
            "key": f"proof:{kv['recipient_id']}",
            "value": kv["expected_proof"],
        })

    with open(output_kv_path, "w", encoding="utf-8") as f:
        json.dump(kv_bulk, f, indent=2)
    print(f"[OK] Preuves KV écrites dans : {output_kv_path}")


def main():
    parser = argparse.ArgumentParser(description="Générateur de coffre-fort ✲ Dead Man's Switch")
    parser.add_argument("--config", "-c", default="recipients.example.json", help="Fichier de configuration")
    parser.add_argument("--output", "-o", default="portal/vault.json", help="Chemin de sortie vault.json")
    parser.add_argument("--kv-output", "-k", default="worker/proofs_seed.json", help="Chemin de sortie KV")
    parser.add_argument("--worker-url", "-w", default="https://dms-ingress.dehoveleho.workers.dev", help="URL du Worker")

    args = parser.parse_args()

    # Conserver le salt existant si présent, sinon générer un nouveau
    global_salt = base64.b64encode(os.urandom(16)).decode("utf-8")
    if os.path.exists(args.output):
        try:
            with open(args.output, "r", encoding="utf-8") as f:
                old = json.load(f)
                if old.get("salt"):
                    global_salt = old["salt"]
        except Exception:
            pass

    print(f"Chargement de {args.config}...")
    public_entries, kv_entries = process_config_file(args.config, global_salt)
    save_vault(public_entries, kv_entries, global_salt, args.output, args.kv_output, args.worker_url)


if __name__ == "__main__":
    main()
