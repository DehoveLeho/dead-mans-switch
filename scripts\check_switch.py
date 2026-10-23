#!/usr/bin/env python3
"""
scripts/check_switch.py
=======================
Autonomous Zero-Knowledge Dead Man's Switch - Evaluation & Dispatch Engine.

Executed periodically by GitHub Actions (.github/workflows/deadmanswitch.yml).

Inactivity Rules:
- < 7 days: Silent exit (system healthy).
- 7 to 29 days: Warning email sent to repository owner.
- >= 30 days: Fetches seen status from Cloudflare Worker KV, filters out
  already-acknowledged recipients, and sends alert emails directing remaining
  recipients to the static decryption portal.
"""

import argparse
import json
import os
import smtplib
import subprocess
import sys
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, Tuple

import requests

WARNING_THRESHOLD_DAYS = 7.0
TRIGGER_THRESHOLD_DAYS = 30.0
SECONDS_PER_DAY = 86400.0


def get_latest_commit_timestamp(repo_dir: str = ".") -> int:
    """
    Retrieves the unix timestamp of the latest commit in the repository.
    Can be overridden by DMS_MOCK_COMMIT_TIMESTAMP environment variable.
    """
    mock_ts = os.environ.get("DMS_MOCK_COMMIT_TIMESTAMP")
    if mock_ts:
        return int(mock_ts)

    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct"],
            cwd=repo_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        ts_str = result.stdout.strip()
        if not ts_str:
            raise ValueError("Empty commit timestamp from git")
        return int(ts_str)
    except Exception as e:
        # Fallback if git binary or repo not in checkout (e.g. tarball)
        print(f"[!] Warning: Could not read git commit timestamp via git CLI ({e}).")
        head_file = os.path.join(repo_dir, ".git", "FETCH_HEAD")
        if os.path.exists(head_file):
            return int(os.path.getmtime(head_file))
        # If nothing found, return current time to avoid accidental triggering
        print("[!] Defaulting to current system time.")
        return int(time.time())


def fetch_worker_status(worker_url: str, status_token: str) -> Dict[str, Any]:
    """
    Fetches the seen status of recipients from the Cloudflare Worker /status endpoint.
    """
    if not worker_url:
        print("[*] No CF_WORKER_URL configured. Assuming no recipients acknowledged yet.")
        return {}

    url = f"{worker_url.rstrip('/')}/status"
    headers = {
        "User-Agent": "DeadMansSwitch-Evaluator/1.0",
    }
    if status_token:
        headers["Authorization"] = f"Bearer {status_token}"

    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            data = response.json()
            return data.get("seen_recipients", {})
        else:
            print(f"[!] Error fetching worker status: HTTP {response.status_code} - {response.text}")
            return {}
    except Exception as err:
        print(f"[!] Network error contacting Cloudflare Worker ({url}): {err}")
        return {}


def send_email_resend(api_key: str, from_addr: str, to_addr: str, subject: str, body_text: str, body_html: Optional[str] = None) -> bool:
    """Sends email via Resend API."""
    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": from_addr or "Dead Man's Switch <notifications@resend.dev>",
        "to": [to_addr],
        "subject": subject,
        "text": body_text,
    }
    if body_html:
        payload["html"] = body_html

    res = requests.post(url, json=payload, headers=headers, timeout=15)
    if res.status_code in (200, 201):
        print(f"[+] Resend email successfully delivered to {to_addr}")
        return True
    else:
        print(f"[!] Resend API error ({res.status_code}): {res.text}")
        return False


def send_email_sendgrid(api_key: str, from_addr: str, to_addr: str, subject: str, body_text: str) -> bool:
    """Sends email via SendGrid API."""
    url = "https://api.sendgrid.com/v3/mail/send"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "personalizations": [{"to": [{"email": to_addr}]}],
        "from": {"email": from_addr or "dms-alerts@example.com"},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body_text}],
    }
    res = requests.post(url, json=payload, headers=headers, timeout=15)
    if res.status_code in (200, 202):
        print(f"[+] SendGrid email delivered to {to_addr}")
        return True
    else:
        print(f"[!] SendGrid error ({res.status_code}): {res.text}")
        return False


def send_email_smtp(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    from_addr: str,
    to_addr: str,
    subject: str,
    body_text: str,
    use_tls: bool = True,
) -> bool:
    """Sends email via SMTP."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr or smtp_user
    msg["To"] = to_addr
    msg.attach(MIMEText(body_text, "plain"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            if use_tls:
                server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(msg["From"], [to_addr], msg.as_string())
        print(f"[+] SMTP email delivered to {to_addr}")
        return True
    except Exception as err:
        print(f"[!] SMTP sending failed to {to_addr}: {err}")
        return False


def dispatch_notification(
    to_email: str,
    subject: str,
    body_text: str,
    dry_run: bool = False,
) -> bool:
    """
    Central dispatcher that automatically detects configured email backend
    (Resend, SendGrid, SMTP, or Dry-run fallback).
    """
    if dry_run or os.environ.get("DMS_DRY_RUN") == "1":
        print(f"[DRY-RUN] Email to: {to_email}")
        print(f"[DRY-RUN] Subject:  {subject}")
        print(f"[DRY-RUN] Body:\n{body_text}\n" + "-" * 50)
        return True

    resend_key = os.environ.get("RESEND_API_KEY")
    sendgrid_key = os.environ.get("SENDGRID_API_KEY")
    smtp_host = os.environ.get("SMTP_HOST")
    from_email = os.environ.get("FROM_EMAIL", "noreply@deadmanswitch.local")

    if resend_key:
        return send_email_resend(resend_key, from_email, to_email, subject, body_text)
    elif sendgrid_key:
        return send_email_sendgrid(sendgrid_key, from_email, to_email, subject, body_text)
    elif smtp_host:
        port = int(os.environ.get("SMTP_PORT", 587))
        user = os.environ.get("SMTP_USER", "")
        password = os.environ.get("SMTP_PASS", "")
        tls = os.environ.get("SMTP_TLS", "1") == "1"
        return send_email_smtp(smtp_host, port, user, password, from_email, to_email, subject, body_text, tls)
    else:
        print(f"[!] No email provider secrets found (RESEND_API_KEY, SENDGRID_API_KEY, or SMTP_HOST).")
        print(f"[!] Emulating email delivery in dry-run mode for {to_email}:")
        print(f"Subject: {subject}\n{body_text}\n")
        return True


def evaluate_inactivity(
    latest_commit_ts: int,
    current_ts: int,
) -> Tuple[str, float]:
    """
    Computes inactivity days and categorizes into:
    - 'HEALTHY' (< 7 days)
    - 'WARNING' (7 to 29.99 days)
    - 'TRIGGERED' (>= 30 days)
    """
    diff_seconds = max(0, current_ts - latest_commit_ts)
    days = diff_seconds / SECONDS_PER_DAY

    if days < WARNING_THRESHOLD_DAYS:
        return "HEALTHY", days
    elif days < TRIGGER_THRESHOLD_DAYS:
        return "WARNING", days
    else:
        return "TRIGGERED", days


def run_evaluation(
    vault_path: str = "portal/vault.json",
    dry_run: bool = False,
    override_commit_ts: Optional[int] = None,
    override_now: Optional[int] = None,
) -> int:
    """
    Main evaluation pipeline.
    Returns exit code (0 for success).
    """
    current_ts = override_now if override_now is not None else int(time.time())
    commit_ts = override_commit_ts if override_commit_ts is not None else get_latest_commit_timestamp()

    status, days = evaluate_inactivity(commit_ts, current_ts)

    print("=" * 65)
    print(" DEAD MAN'S SWITCH - SYSTEM EVALUATION")
    print("=" * 65)
    print(f"Latest Commit Timestamp: {commit_ts} ({time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(commit_ts))})")
    print(f"Current Evaluator Time:  {current_ts} ({time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(current_ts))})")
    print(f"Inactivity Duration:     {days:.2f} days")
    print(f"Evaluation Status:       [{status}]")
    print("=" * 65)

    owner_email = os.environ.get("OWNER_EMAIL", "")
    cf_worker_url = os.environ.get("CF_WORKER_URL", "")
    cf_status_token = os.environ.get("CF_STATUS_TOKEN", "")
    portal_url = os.environ.get("PORTAL_URL", "https://your-domain.pages.dev")
    ping_url = os.environ.get("HEARTBEAT_URL", f"{cf_worker_url.rstrip('/')}/ping?token={os.environ.get('HEARTBEAT_SECRET', '')}")

    # Case 1: Healthy (< 7 days)
    if status == "HEALTHY":
        print(f"[OK] Heartbeat active. Inactivity is {days:.2f} days (< 7.00 days). Silent exit.")
        return 0

    # Case 2: Warning (7 to 29.99 days)
    if status == "WARNING":
        print(f"[!] Warning state active: {days:.2f} days of inactivity.")
        if not owner_email:
            print("[!] Warning: OWNER_EMAIL environment variable is not set. Cannot send warning reminder.")
            return 0

        subject = f"[URGENT] Dead Man's Switch Warning: {days:.1f} Days Inactive"
        body = (
            f"Hello,\n\n"
            f"This is an automated alert from your autonomous Dead Man's Switch system.\n\n"
            f"No heartbeat has been registered for {days:.1f} days.\n"
            f"The system is configured to trigger payload release after {TRIGGER_THRESHOLD_DAYS:.0f} days of inactivity.\n\n"
            f"To check in and reset your heartbeat timer, simply visit your secret ping URL:\n"
            f"{ping_url}\n\n"
            f"If you do not check in within {TRIGGER_THRESHOLD_DAYS - days:.1f} days, alert emails with access "
            f"instructions will be dispatched to your designated recipients.\n\n"
            f"Stay safe,\nYour Dead Man's Switch Engine"
        )
        dispatch_notification(owner_email, subject, body, dry_run=dry_run)
        return 0

    # Case 3: Triggered (>= 30 days)
    print(f"[CRITICAL] Dead Man's Switch TRIGGERED! Inactivity {days:.2f} days >= {TRIGGER_THRESHOLD_DAYS} days.")

    # 1. Fetch seen status from Cloudflare Worker KV
    seen_map = fetch_worker_status(cf_worker_url, cf_status_token)
    print(f"[*] Cloudflare KV reported {len(seen_map)} acknowledged recipient(s): {list(seen_map.keys())}")

    # 2. Read public vault to identify recipients
    if not os.path.exists(vault_path):
        print(f"[!] Critical Error: Vault file '{vault_path}' does not exist.")
        return 1

    with open(vault_path, "r", encoding="utf-8") as f:
        vault_data = json.load(f)

    recipients = vault_data.get("recipients", [])
    if not recipients:
        print("[!] No recipients configured in vault.")
        return 0

    unseen_count = 0
    acknowledged_count = 0

    for r in recipients:
        r_id = r["id"].strip().lower()
        r_name = r.get("name", r_id)
        r_email = r.get("email", "")

        if not r_email:
            print(f"[-] Recipient '{r_id}' has no email configured. Skipping.")
            continue

        # Check if recipient already acknowledged decryption
        seen_entry = seen_map.get(r_id)
        if seen_entry and seen_entry.get("seen") is True:
            seen_time = seen_entry.get("iso", "previously")
            print(f"[SKIP] Recipient '{r_id}' ({r_email}) already decrypted their message (acknowledged: {seen_time}).")
            acknowledged_count += 1
            continue

        unseen_count += 1
        access_url = f"{portal_url.rstrip('/')}/?recipient={r_id}"

        subject = f"Message confidentiel pour {r_name}"
        body = (
            f"Bonjour {r_name},\n\n"
            f"Un message confidentiel vous a été laissé à l'adresse suivante :\n"
            f"{portal_url}\n\n"
            f"Pour y accéder :\n"
            f"1. Ouvrez le lien : {portal_url}\n"
            f"2. Écrivez votre prénom ('{r_name.split()[0]}')\n"
            f"3. Cliquez sur votre nom et saisissez votre mot de passe pour lire le document.\n"
        )

        print(f"[+] Sending payload release notification to '{r_name}' <{r_email}>...")
        dispatch_notification(r_email, subject, body, dry_run=dry_run)

    print("=" * 65)
    print(f"Evaluation complete. Total: {len(recipients)} | Notified: {unseen_count} | Already Acknowledged: {acknowledged_count}")
    print("=" * 65)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Dead Man's Switch Evaluation Script")
    parser.add_argument("--vault", default="portal/vault.json", help="Path to vault.json")
    parser.add_argument("--dry-run", action="store_true", help="Run without sending real emails")
    parser.add_argument("--commit-timestamp", type=int, help="Override commit unix timestamp for testing")
    parser.add_argument("--now", type=int, help="Override current evaluator unix timestamp for testing")

    args = parser.parse_args()
    code = run_evaluation(
        vault_path=args.vault,
        dry_run=args.dry_run,
        override_commit_ts=args.commit_timestamp,
        override_now=args.now,
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
