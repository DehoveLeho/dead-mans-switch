#!/usr/bin/env python3
"""
scripts/deploy.py
=================
Autonomous Zero-Knowledge Dead Man's Switch - Automated Deployment Tool.

Deploys Cloudflare Worker, KV Namespace, seed proofs, and static portal directly
using Cloudflare & GitHub REST APIs (100% pure Python, no npm/node required).
"""

import argparse
import getpass
import json
import os
import sys
import secrets
from typing import Any, Dict, List, Optional

import requests


def cf_request(method: str, path: str, token: str, data: Optional[Dict] = None, files: Optional[Dict] = None) -> Dict[str, Any]:
    url = f"https://api.cloudflare.com/client/v4{path}"
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None and files is None:
        headers["Content-Type"] = "application/json"
        res = requests.request(method, url, headers=headers, json=data, timeout=30)
    elif files is not None:
        res = requests.request(method, url, headers=headers, data=data, files=files, timeout=30)
    else:
        res = requests.request(method, url, headers=headers, timeout=30)

    try:
        json_resp = res.json()
    except Exception:
        raise RuntimeError(f"Cloudflare API returned non-JSON: HTTP {res.status_code} - {res.text}")

    if not json_resp.get("success", False) and res.status_code not in (200, 201):
        errors = json_resp.get("errors", [])
        err_msg = "; ".join([e.get("message", str(e)) for e in errors]) if errors else res.text
        raise RuntimeError(f"Cloudflare API Error ({res.status_code}): {err_msg}")

    return json_resp


def get_cf_account_id(token: str) -> str:
    print("[*] Verifying Cloudflare API token and listing accounts...")
    res = cf_request("GET", "/accounts", token)
    accounts = res.get("result", [])
    if not accounts:
        raise RuntimeError("No Cloudflare accounts found for this API token.")
    
    account_id = accounts[0]["id"]
    account_name = accounts[0]["name"]
    print(f"[+] Using Cloudflare Account: {account_name} ({account_id})")
    return account_id


def create_or_get_kv_namespace(account_id: str, token: str, title: str = "DMS_KV") -> str:
    print(f"[*] Checking Cloudflare KV namespace '{title}'...")
    res = cf_request("GET", f"/accounts/{account_id}/storage/kv/namespaces", token)
    for ns in res.get("result", []):
        if ns.get("title") == title:
            print(f"[+] Found existing KV namespace: {title} ({ns['id']})")
            return ns["id"]

    print(f"[*] Creating new KV namespace '{title}'...")
    create_res = cf_request("POST", f"/accounts/{account_id}/storage/kv/namespaces", token, data={"title": title})
    ns_id = create_res["result"]["id"]
    print(f"[+] Created KV namespace: {title} ({ns_id})")
    return ns_id


def seed_kv_proofs(account_id: str, token: str, kv_id: str, seed_file: str = "worker/proofs_seed.json"):
    if not os.path.exists(seed_file):
        print(f"[!] Warning: Seed file {seed_file} does not exist. Skipping proof seeding.")
        return

    with open(seed_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not data:
        print("[!] No keys to seed.")
        return

    print(f"[*] Seeding {len(data)} cryptographic proofs into KV namespace...")
    cf_request("PUT", f"/accounts/{account_id}/storage/kv/namespaces/{kv_id}/bulk", token, data=data)
    print(f"[+] Successfully seeded {len(data)} keys into Cloudflare KV.")


def deploy_worker(
    account_id: str,
    token: str,
    kv_id: str,
    script_name: str = "dms-ingress",
    heartbeat_secret: str = "",
    status_token: str = "",
    github_pat: str = "",
    github_owner: str = "",
    github_repo: str = "",
):
    worker_js_path = "worker/src/index.js"
    if not os.path.exists(worker_js_path):
        raise FileNotFoundError(f"Worker file {worker_js_path} not found.")

    with open(worker_js_path, "r", encoding="utf-8") as f:
        code = f.read()

    print(f"[*] Deploying Cloudflare Worker '{script_name}'...")

    bindings = [
        {"name": "DMS_KV", "type": "kv_namespace", "namespace_id": kv_id},
        {"name": "HEARTBEAT_SECRET", "type": "secret_text", "text": heartbeat_secret},
        {"name": "STATUS_TOKEN", "type": "secret_text", "text": status_token},
        {"name": "GITHUB_PAT", "type": "secret_text", "text": github_pat},
        {"name": "GITHUB_OWNER", "type": "plain_text", "text": github_owner},
        {"name": "GITHUB_REPO", "type": "plain_text", "text": github_repo},
        {"name": "GITHUB_WORKFLOW", "type": "plain_text", "text": "heartbeat.yml"},
        {"name": "GITHUB_BRANCH", "type": "plain_text", "text": "main"},
    ]

    metadata = {
        "main_module": "index.js",
        "bindings": bindings,
        "compatibility_date": "2024-01-01",
    }

    files = {
        "metadata": ("metadata.json", json.dumps(metadata), "application/json"),
        "index.js": ("index.js", code, "application/javascript+module"),
    }

    cf_request("PUT", f"/accounts/{account_id}/workers/scripts/{script_name}", token, files=files)
    print(f"[+] Worker script '{script_name}' successfully deployed!")

    # Enable workers.dev subdomain route if available
    try:
        cf_request("POST", f"/accounts/{account_id}/workers/scripts/{script_name}/subdomain", token, data={"enabled": True})
        print(f"[+] Enabled workers.dev route for '{script_name}'")
    except Exception as e:
        print(f"[*] Subdomain activation note: {e}")


def interactive_deploy():
    print("=" * 65)
    print("  AUTONOMOUS DEAD MAN'S SWITCH - DEPLOYMENT WIZARD")
    print("=" * 65)
    print("This tool will deploy your Worker, KV, and configurations to Cloudflare.")
    print()

    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN") or input("Enter Cloudflare API Token (with Workers/KV permissions): ").strip()
    if not cf_token:
        print("[!] Cloudflare API Token is required. Exiting.")
        return

    account_id = get_cf_account_id(cf_token)
    kv_id = create_or_get_kv_namespace(account_id, cf_token, "DMS_KV")
    seed_kv_proofs(account_id, cf_token, kv_id)

    print()
    print("Configuring Secrets & GitHub Integration:")
    heartbeat_secret = os.environ.get("HEARTBEAT_SECRET") or secrets.token_hex(24)
    status_token = os.environ.get("STATUS_TOKEN") or secrets.token_hex(24)
    print(f"[+] Generated HEARTBEAT_SECRET: {heartbeat_secret}")
    print(f"[+] Generated STATUS_TOKEN:     {status_token}")

    gh_owner = input("Enter your GitHub username or organization: ").strip()
    gh_repo = input("Enter your private GitHub repo name (e.g. dead-mans-switch): ").strip()
    gh_pat = getpass.getpass("Enter your GitHub PAT (Personal Access Token, repo+actions:write): ").strip()

    deploy_worker(
        account_id=account_id,
        token=cf_token,
        kv_id=kv_id,
        heartbeat_secret=heartbeat_secret,
        status_token=status_token,
        github_pat=gh_pat,
        github_owner=gh_owner,
        github_repo=gh_repo,
    )

    print()
    print("=" * 65)
    print(" DEPLOYMENT COMPLETE & SECRETS SUMMARY")
    print("=" * 65)
    print("Configure these secrets in your GitHub Repository (Settings -> Secrets -> Actions):")
    print(f"  CF_STATUS_TOKEN:  {status_token}")
    print(f"  HEARTBEAT_SECRET: {heartbeat_secret}")
    print()
    print("Your Heartbeat Check-in URL (Bookmark this on your browser / phone):")
    print(f"  https://dms-ingress.<your-subdomain>.workers.dev/ping?token={heartbeat_secret}")
    print("=" * 65)


def main():
    parser = argparse.ArgumentParser(description="Deploy Dead Man's Switch to Cloudflare")
    parser.add_argument("--interactive", "-i", action="store_true", default=True, help="Run interactive deployment")
    args = parser.parse_args()
    interactive_deploy()


if __name__ == "__main__":
    main()
