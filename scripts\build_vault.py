#!/usr/bin/env python3
"""
scripts/build_vault.py
======================
Autonomous Zero-Knowledge Dead Man's Switch - Offline Vault Generator CLI.

Features:
- Encrypts individualized recipient messages offline before committing.
- Derives a 256-bit AES key using PBKDF2 (SHA-256, 250,000 iterations) with a unique 16-byte random salt per recipient.
- Encrypts plaintext payloads using AES-256-GCM (12-byte random IV/nonce).
- Appends the 16-byte authentication tag directly to ciphertext (compatible with Web Crypto API).
- Generates expected proof: SHA-256(passphrase + salt) stored in Cloudflare KV to validate legitimate reads.
- Exports public vault to portal/vault.json and outputs KV seeding data for Cloudflare Workers.
"""

import argparse
import getpass
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERATIONS = 250_000
KEY_LENGTH_BYTES = 32  # 256 bits
SALT_LENGTH_BYTES = 16  # 128 bits
IV_LENGTH_BYTES = 12  # 96 bits for AES-GCM


def derive_key(passphrase: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    """Derive a 256-bit AES key using PBKDF2 with SHA-256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH_BYTES,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def calculate_proof(passphrase: str, salt: bytes) -> str:
    """
    Calculate the zero-knowledge acknowledgment proof:
    SHA-256(passphrase_utf8 + salt_bytes).
    Returns lower-case 64-character hex string.
    """
    hasher = hashlib.sha256()
    hasher.update(passphrase.encode("utf-8"))
    hasher.update(salt)
    return hasher.hexdigest()


def encrypt_payload(plaintext: str, passphrase: str) -> Tuple[str, str, str, str]:
    """
    Encrypt plaintext using PBKDF2 and AES-256-GCM.
    Returns: (salt_hex, iv_hex, ciphertext_hex, proof_hex)
    where ciphertext_hex includes the 16-byte GCM authentication tag appended.
    """
    salt = os.urandom(SALT_LENGTH_BYTES)
    iv = os.urandom(IV_LENGTH_BYTES)
    key = derive_key(passphrase, salt)
    
    aesgcm = AESGCM(key)
    # encrypt returns ciphertext + 16-byte authentication tag
    raw_ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    proof_hex = calculate_proof(passphrase, salt)

    return (
        salt.hex(),
        iv.hex(),
        raw_ciphertext.hex(),
        proof_hex,
    )


def decrypt_payload(salt_hex: str, iv_hex: str, ciphertext_hex: str, passphrase: str) -> str:
    """
    Decrypt payload given hex salt, iv, ciphertext, and passphrase.
    Raises exception if passphrase is incorrect or ciphertext has been tampered with.
    """
    salt = bytes.fromhex(salt_hex)
    iv = bytes.fromhex(iv_hex)
    raw_ciphertext = bytes.fromhex(ciphertext_hex)
    
    key = derive_key(passphrase, salt)
    aesgcm = AESGCM(key)
    decrypted_bytes = aesgcm.decrypt(iv, raw_ciphertext, None)
    return decrypted_bytes.decode("utf-8")


def build_recipient_entry(
    recipient_id: str,
    name: str,
    email: str,
    message: str,
    passphrase: str,
    hint: str = "",
    photo: str = "",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Creates encrypted recipient object for vault.json and KV entry for worker.
    """
    salt_hex, iv_hex, ciphertext_hex, proof_hex = encrypt_payload(message, passphrase)

    # Verify self-test immediately
    verification = decrypt_payload(salt_hex, iv_hex, ciphertext_hex, passphrase)
    assert verification == message, "Encryption verification failed: decrypted text does not match original message"

    public_entry = {
        "id": recipient_id.strip().lower(),
        "name": name.strip(),
        "email": email.strip().lower(),
        "hint": hint.strip(),
        "photo": photo.strip(),
        "salt": salt_hex,
        "iv": iv_hex,
        "ciphertext": ciphertext_hex,
    }

    kv_entry = {
        "recipient_id": recipient_id.strip().lower(),
        "expected_proof": proof_hex,
        "email": email.strip().lower(),
        "name": name.strip(),
    }

    return public_entry, kv_entry


def interactive_wizard() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Interactive command-line wizard for configuring recipients."""
    print("=" * 65)
    print(" Autonomous Zero-Knowledge Dead Man's Switch - Vault Generator")
    print("=" * 65)
    print("Enter recipient details. Passphrases are never saved or committed.")
    print()

    public_recipients: List[Dict[str, Any]] = []
    kv_recipients: List[Dict[str, Any]] = []

    while True:
        recipient_id = input("Recipient Identifier (e.g., alice, bob, lawyer): ").strip()
        if not recipient_id:
            print("Identifier cannot be empty.")
            continue

        name = input(f"Full Name for '{recipient_id}': ").strip()
        email = input(f"Notification Email for '{recipient_id}': ").strip()
        hint = input("Passphrase Hint (visible in portal, e.g., 'Mom's maiden name + year'): ").strip()

        print(f"Enter confidential message for '{recipient_id}'.")
        print("Type or paste message, then type EOF on a single line or press Enter:")
        lines = []
        while True:
            try:
                line = input()
                if line == "EOF":
                    break
                lines.append(line)
            except EOFError:
                break
        message = "\n".join(lines).strip()
        if not message:
            print("Warning: message was empty.")

        while True:
            passphrase = getpass.getpass(f"Enter secret passphrase for '{recipient_id}': ")
            confirm = getpass.getpass("Confirm secret passphrase: ")
            if passphrase != confirm:
                print("Passphrases do not match. Try again.")
                continue
            if len(passphrase) < 8:
                print("Passphrase should be at least 8 characters.")
                continue
            break

        pub_entry, kv_entry = build_recipient_entry(recipient_id, name, email, message, passphrase, hint)
        public_recipients.append(pub_entry)
        kv_recipients.append(kv_entry)
        print(f"[+] Successfully encrypted message for recipient '{recipient_id}'.")
        print(f"    Salt: {pub_entry['salt'][:8]}...")
        print(f"    Expected Proof: {kv_entry['expected_proof']}")
        print()

        more = input("Add another recipient? (y/N): ").strip().lower()
        if more != "y":
            break

    return public_recipients, kv_recipients


def process_config_file(config_path: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Load recipients and messages from a local JSON configuration file."""
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_recipients = data if isinstance(data, list) else data.get("recipients", [])
    public_recipients: List[Dict[str, Any]] = []
    kv_recipients: List[Dict[str, Any]] = []

    for item in raw_recipients:
        rec_id = item["id"]
        name = item.get("name", rec_id)
        email = item.get("email", "")
        hint = item.get("hint", "")
        photo = item.get("photo", "")
        message = item["message"]
        passphrase = item["passphrase"]

        pub_entry, kv_entry = build_recipient_entry(rec_id, name, email, message, passphrase, hint, photo)
        public_recipients.append(pub_entry)
        kv_recipients.append(kv_entry)
        print(f"[+] Encrypted message for '{rec_id}' (email: {email})")

    return public_recipients, kv_recipients


def save_vault(
    public_recipients: List[Dict[str, Any]],
    kv_recipients: List[Dict[str, Any]],
    output_vault_path: str = "portal/vault.json",
    output_kv_path: str = "worker/proofs_seed.json",
    worker_url: str = "",
) -> None:
    """Save public vault JSON and worker KV seed file."""
    os.makedirs(os.path.dirname(os.path.abspath(output_vault_path)), exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(output_kv_path)), exist_ok=True)

    vault_data = {
        "version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pbkdf2_iterations": PBKDF2_ITERATIONS,
        "worker_url": worker_url,
        "recipients": public_recipients,
    }

    with open(output_vault_path, "w", encoding="utf-8") as f:
        json.dump(vault_data, f, indent=2)
    print(f"\n[OK] Public vault written to: {output_vault_path}")

    # Build KV key-value mapping for Cloudflare KV bulk put
    kv_bulk = []
    for kv in kv_recipients:
        kv_bulk.append({
            "key": f"proof:{kv['recipient_id']}",
            "value": kv["expected_proof"],
        })
        # Optional metadata
        kv_bulk.append({
            "key": f"meta:{kv['recipient_id']}",
            "value": json.dumps({"name": kv["name"], "email": kv["email"]}),
        })

    with open(output_kv_path, "w", encoding="utf-8") as f:
        json.dump(kv_bulk, f, indent=2)
    print(f"[OK] KV Seed data written to: {output_kv_path}")

    # Print summary and CLI helper commands
    print("\n" + "=" * 65)
    print(" CLOUDFLARE KV SEEDING INSTRUCTIONS")
    print("=" * 65)
    print("To upload expected cryptographic proofs into your Cloudflare KV namespace, run:")
    print(f"  npx wrangler kv bulk put --binding=DMS_KV {output_kv_path}")
    print()
    print("Or seed individual keys manually:")
    for kv in kv_recipients:
        print(f"  npx wrangler kv key put --binding=DMS_KV \"proof:{kv['recipient_id']}\" \"{kv['expected_proof']}\"")
    print("=" * 65)


def main():
    parser = argparse.ArgumentParser(description="Autonomous Dead Man's Switch - Offline Vault Builder")
    parser.add_argument("--config", "-c", help="Path to input recipients JSON file (offline template)")
    parser.add_argument("--output", "-o", default="portal/vault.json", help="Path to output public vault.json")
    parser.add_argument("--kv-output", "-k", default="worker/proofs_seed.json", help="Path to output Cloudflare KV seed JSON")
    parser.add_argument("--worker-url", "-w", default="", help="Cloudflare Worker base URL (e.g. https://dms-ingress.yourname.workers.dev)")
    parser.add_argument("--interactive", "-i", action="store_true", help="Launch interactive wizard")

    args = parser.parse_args()

    if args.config:
        print(f"Loading configuration from {args.config}...")
        public_recipients, kv_recipients = process_config_file(args.config)
    elif args.interactive or not sys.stdin.isatty():
        public_recipients, kv_recipients = interactive_wizard()
    else:
        # Default fallback to sample generation or prompt
        print("No --config specified. Starting interactive wizard...")
        public_recipients, kv_recipients = interactive_wizard()

    save_vault(public_recipients, kv_recipients, args.output, args.kv_output, args.worker_url)


if __name__ == "__main__":
    main()
