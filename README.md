# Autonomous Zero-Knowledge Dead Man's Switch System

> A 100% free, fully autonomous, zero-knowledge Dead Man's Switch built on **GitHub Actions**, **Cloudflare Workers**, **Cloudflare KV**, and a static **Web Crypto API** portal.

---

## 1. Architecture Overview

The system consists of four decoupled subsystems designed for maximum privacy, zero server-side decryption dependencies, and zero operational costs:

```mermaid
flowchart TD
    subgraph Subsystem1["1. Heartbeat & Ingress Controller (Cloudflare Worker + KV)"]
        Owner["Owner Check-in (Browser/Bookmark)"] -->|GET /ping?token=SECRET| Worker["Cloudflare Worker"]
        Worker -->|workflow_dispatch (PAT)| GH_Action["GitHub Actions: heartbeat.yml"]
        Worker -->|Read / Write| CF_KV[("Cloudflare KV Namespace")]
    end

    subgraph Subsystem2["2. Cron Scheduler & Evaluator (GitHub Actions + Python)"]
        GH_Cron["Weekly Cron Trigger"] --> Evaluator["check_switch.py"]
        Evaluator -->|git log -1 --format=%ct| RepoHistory["Repository Git History"]
        Evaluator -->|< 7 days: Silent Exit| Silent["Healthy (No-op)"]
        Evaluator -->|7 to 29 days: Warning| OwnerAlert["Owner Email Alert"]
        Evaluator -->|>= 30 days: Triggered| QueryKV["Query /status from Worker"]
        QueryKV --> FilterSeen{"Has Recipient Acknowledged?"}
        FilterSeen -->|Yes| Skip["Skip (Already Decrypted)"]
        FilterSeen -->|No| RecipientEmail["Dispatch Recipient Access Email"]
    end

    subgraph Subsystem3["3. Vault Generator CLI (Offline Python)"]
        RecipientsRaw["Offline Secrets & Messages"] --> VaultCLI["build_vault.py"]
        VaultCLI -->|PBKDF2 250k + AES-256-GCM| PublicVault["portal/vault.json"]
        VaultCLI -->|SHA-256(passphrase + salt)| ProofsKV["worker/proofs_seed.json"]
        ProofsKV -->|wrangler kv bulk put| CF_KV
    end

    subgraph Subsystem4["4. Static Decryption Web Portal (Web Crypto API)"]
        Recipient["Recipient Browser"] --> Portal["portal/index.html (Pages)"]
        Portal -->|Fetch| PublicVault
        Recipient -->|Input Secret Passphrase| BrowserCrypto["Web Crypto API (crypto.subtle)"]
        BrowserCrypto -->|PBKDF2 Key Derivation + AES-GCM Decrypt| Plaintext["Decrypted Confidential Payload"]
        BrowserCrypto -->|Compute SHA-256(passphrase + salt)| ProofHash["Cryptographic Proof"]
        ProofHash -->|POST /ack| Worker
        Worker -->|Store seen:recipient_id| CF_KV
    end
```

---

## 2. Subsystems Detail

### Subsystem 1: Heartbeat & Ingress Controller (`worker/`)
- **Runtime**: Cloudflare Worker (TypeScript) backed by Cloudflare Workers KV.
- **Endpoints**:
  - `GET /ping?token=<SECRET>`: Browser-accessible check-in endpoint. Upon receiving a valid token, it dispatches GitHub Actions (`heartbeat.yml`) using a Personal Access Token (PAT) to commit an empty commit to the private repo, resetting the clock. Also presents a confirmation UI for web visits.
  - `POST /ack`: Receives client-side cryptographic decryption proofs (`{ recipient_id, proof }`) from recipients, verifies against expected proof in KV, and sets timestamped record `seen:<recipient_id>`.
  - `GET /status`: Protected route queried by GitHub Actions to inspect which recipients have already viewed their payloads.
  - `POST /seed`: Protected administrative route to bulk-seed expected cryptographic proofs.

### Subsystem 2: Cron Scheduler & Evaluator (`scripts/check_switch.py`)
- **Runtime**: GitHub Actions (`.github/workflows/deadmanswitch.yml`) running on a weekly cron (and manual dispatch).
- **Inactivity Logic**:
  - `< 7 days`: System healthy. Silent exit (exit code 0).
  - `>= 7 days and < 30 days`: Warning state. Dispatches reminder email to repository owner with the heartbeat link.
  - `>= 30 days`: Switch triggered. Queries Cloudflare Worker `/status`, filters out recipients who have already acknowledged their messages (`seen: true`), and sends notification emails with access links only to remaining recipients.
- **Email Backends**: Built-in support for **Resend**, **SendGrid**, standard **SMTP** (Gmail App Passwords, Fastmail, Brevo, etc.), or offline dry-run mode.

### Subsystem 3: Vault Generator CLI (`scripts/build_vault.py`)
- **Runtime**: Python 3 offline CLI. Never touches the internet or commits plaintexts.
- **Cryptographic Specifications**:
  - **Key Derivation**: PBKDF2 (SHA-256, 250,000 iterations) with a unique 16-byte cryptographically secure random salt per recipient (`os.urandom(16)`).
  - **Payload Encryption**: AES-256-GCM with a 12-byte random IV (`os.urandom(12)`) and a 16-byte authentication tag appended.
  - **Zero-Knowledge Acknowledgment Proof**: `SHA-256(passphrase.encode('utf-8') + salt)`. Preimage-resistant hash stored in KV to validate legitimate client reads without exposing passphrases.
- **Output**: Writes public encrypted vault to `portal/vault.json` and Cloudflare KV seed to `worker/proofs_seed.json`.

### Subsystem 4: Static Decryption Web Portal (`portal/`)
- **Hosting**: Cloudflare Pages or GitHub Pages (100% static, free HTTPS).
- **Runtime**: Vanilla HTML5, CSS3, and JavaScript using browser-native `crypto.subtle`.
- **Zero-Knowledge Decryption**:
  - Browser imports passphrase, derives 256-bit AES key via PBKDF2 (SHA-256, 250,000 iterations).
  - Decrypts ciphertext and verifies GCM authentication tag in browser memory.
  - Calculates proof `SHA-256(passphrase + salt)` and fires background `POST /ack` to Cloudflare Worker to stop further email alerts.
  - Auto-selects recipient from URL fragment or query parameter (`?recipient=alice`).

---

## 3. Repository Structure

```text
.
├── .github/
│   └── workflows/
│       ├── deadmanswitch.yml    # Cron evaluation workflow
│       └── heartbeat.yml        # Workflow triggered by Cloudflare Worker
├── worker/
│   ├── wrangler.toml            # Cloudflare Worker & KV configuration
│   ├── package.json             # Worker dependencies and scripts
│   ├── tsconfig.json            # TypeScript configuration
│   ├── proofs_seed.json         # Generated KV seed data (bulk put)
│   └── src/
│       └── index.ts             # Cloudflare Worker entrypoint (TypeScript)
├── portal/
│   ├── index.html               # Decryption web UI
│   ├── style.css                # Clean, responsive dark-mode styling
│   ├── app.js                   # Web Crypto API decryption & /ack dispatcher
│   └── vault.json               # Generated encrypted payloads
├── scripts/
│   ├── check_switch.py          # Python evaluation script executed by GitHub Actions
│   └── build_vault.py           # Offline encryption CLI
├── tests/
│   ├── test_crypto.py           # Unit tests validating cross-compatibility (Python vs Web Crypto)
│   └── test_switch.py           # Unit tests for threshold and seen-state logic
├── recipients.example.json      # Offline template for recipient configuration
├── requirements.txt             # Python dependencies (cryptography, requests, pytest)
├── pytest.ini                   # Pytest configuration
└── README.md                    # Setup, secret provisioning, and usage instructions
```

---

## 4. Step-by-Step Setup Guide

### Step 1: Create Private GitHub Repository
1. Create a new **Private** GitHub repository (e.g. `dead-mans-switch`).
2. Clone it to your local machine:
   ```bash
   git clone git@github.com:your-user/dead-mans-switch.git
   cd dead-mans-switch
   ```
3. Generate a GitHub Personal Access Token (PAT):
   - Navigate to GitHub **Settings** &rarr; **Developer Settings** &rarr; **Personal Access Tokens**.
   - Create a Fine-grained or Classic token with permissions:
     - `Contents`: Read and write
     - `Workflows`: Read and write

---

### Step 2: Deploy Cloudflare Worker & KV (Free Tier)
1. Install [Wrangler CLI](https://developers.cloudflare.com/workers/wrangler/install-and-update/):
   ```bash
   npm install -g wrangler
   wrangler login
   ```
2. Create the Cloudflare KV namespace:
   ```bash
   wrangler kv namespace create DMS_KV
   ```
   Copy the `id` from the output into `worker/wrangler.toml`:
   ```toml
   [[kv_namespaces]]
   binding = "DMS_KV"
   id = "<PASTE_YOUR_KV_NAMESPACE_ID_HERE>"
   ```
3. Configure repository variables in `worker/wrangler.toml`:
   ```toml
   [vars]
   GITHUB_OWNER = "your-github-username"
   GITHUB_REPO = "dead-mans-switch"
   GITHUB_WORKFLOW = "heartbeat.yml"
   GITHUB_BRANCH = "main"
   ```
4. Set sensitive secrets on Cloudflare Worker:
   ```bash
   cd worker
   wrangler secret put HEARTBEAT_SECRET   # Generate a long random secret (e.g., openssl rand -hex 32)
   wrangler secret put STATUS_TOKEN       # Generate a secret token for GitHub Actions to query /status
   wrangler secret put GITHUB_PAT         # Paste your GitHub Personal Access Token
   ```
5. Deploy the worker:
   ```bash
   wrangler deploy
   ```
   Note your worker URL: `https://dms-ingress.<your-subdomain>.workers.dev`.

---

### Step 3: Configure GitHub Repository Secrets
Navigate to **Settings** &rarr; **Secrets and variables** &rarr; **Actions** in your GitHub repository and add the following repository secrets:

| Secret Name | Description | Example / Format |
|---|---|---|
| `CF_WORKER_URL` | Cloudflare Worker URL | `https://dms-ingress.yourname.workers.dev` |
| `CF_STATUS_TOKEN` | Token to query `/status` | Same string provided to `STATUS_TOKEN` |
| `HEARTBEAT_SECRET` | Secret token for `/ping` | Same string provided to `HEARTBEAT_SECRET` |
| `OWNER_EMAIL` | Repository owner's email | `owner@example.com` |
| `PORTAL_URL` | URL of the static decryption portal | `https://your-dms.pages.dev` |
| `FROM_EMAIL` | Sender email address | `dms@yourdomain.com` or `notifications@resend.dev` |
| `RESEND_API_KEY` *(Recommended)* | Resend API key (Free: 3,000 emails/mo) | `re_123456789...` |
| `SENDGRID_API_KEY` *(Optional)* | SendGrid API key | `SG.123456...` |
| `SMTP_HOST` *(Optional)* | SMTP server host | `smtp.gmail.com` |
| `SMTP_PORT` *(Optional)* | SMTP port | `587` |
| `SMTP_USER` *(Optional)* | SMTP username | `user@gmail.com` |
| `SMTP_PASS` *(Optional)* | SMTP password (App Password) | `abcd efgh ijkl mnop` |

---

### Step 4: Generate Encrypted Vault & Seed Proofs Offline
1. Install Python dependencies locally:
   ```bash
   pip install -r requirements.txt
   ```
2. Prepare your confidential recipient configuration using `recipients.example.json` or launch the interactive wizard:
   ```bash
   # Option A: Using a configuration file
   python scripts/build_vault.py --config recipients.example.json --worker-url "https://dms-ingress.yourname.workers.dev"

   # Option B: Interactive CLI wizard
   python scripts/build_vault.py --interactive --worker-url "https://dms-ingress.yourname.workers.dev"
   ```
   This generates:
   - `portal/vault.json`: The public encrypted payload (committed to git).
   - `worker/proofs_seed.json`: Expected zero-knowledge proofs.
3. Upload the proofs into Cloudflare KV:
   ```bash
   npx wrangler kv bulk put --binding=DMS_KV worker/proofs_seed.json
   ```
   *(Or make a `POST /seed` request with `Authorization: Bearer <STATUS_TOKEN>`)*.
4. Safely delete or securely store the plaintext configuration:
   ```bash
   rm recipients.example.json
   ```

---

### Step 5: Deploy the Static Decryption Web Portal
You can host `portal/` on **Cloudflare Pages** or **GitHub Pages** (both 100% free with SSL):

#### Deploy via Cloudflare Pages:
```bash
npx wrangler pages deploy portal --project-name=dms-portal
```
Your portal will be live at `https://dms-portal.pages.dev`.

#### Deploy via GitHub Pages:
- In GitHub Repo Settings &rarr; **Pages**, configure source to deploy from branch or GitHub Actions pointing to the `portal` directory.

---

## 5. Daily Operation & Proof of Life

### Registering a Heartbeat
Bookmark your secret ping URL in your browser, or add a shortcut to your mobile home screen:
```text
https://dms-ingress.yourname.workers.dev/ping?token=<YOUR_HEARTBEAT_SECRET>
```

When visited:
1. Cloudflare Worker verifies the token.
2. Dispatches `heartbeat.yml` on GitHub Actions.
3. GitHub Actions pushes an empty commit to the repository:
   ```text
   Heartbeat: proof of life at 2026-10-23T15:54:59Z [skip ci]
   ```
4. Returns a confirmation screen displaying reset status.

---

## 6. How Decryption & Zero-Knowledge Verification Work

```text
[ Recipient receives alert email ]
               │
               ▼
   Clicks portal link: https://dms-portal.pages.dev/?recipient=alice
               │
               ▼
   Enters pre-shared passphrase
               │
               ▼
   [ Browser Web Crypto API (Client-side) ]
   1. Imports passphrase
   2. Derives 256-bit AES key via PBKDF2 (SHA-256, 250,000 iterations)
   3. Decrypts ciphertext with AES-256-GCM
   4. Plaintext message displayed in browser memory
   5. Computes SHA-256(passphrase + salt)
               │
               ▼
   Background POST /ack { recipient_id: "alice", proof: "<hash>" }
               │
               ▼
   [ Cloudflare Worker ]
   1. Checks proof against KV proof:alice
   2. Stores seen:alice { seen: true, timestamp: ... }
               │
               ▼
   [ Future GitHub Actions Evaluator Runs ]
   1. Fetches /status
   2. Sees seen:alice == true
   3. Skips Alice from all subsequent email reminders!
```

---

## 7. Running Tests & Verifications

Run the automated test suite locally:
```bash
pytest -v
```

All 25 test cases cover:
- Cross-compatibility between Python and Web Crypto API.
- PBKDF2 key derivation and AES-256-GCM authentication tag verification.
- Zero-knowledge proof SHA-256 calculations.
- Healthy (< 7 days), Warning (7–30 days), and Triggered (>= 30 days) evaluation states.
- Filtering out already-acknowledged recipients via Cloudflare Worker status.
- Resend, SendGrid, and SMTP email backends.

To test the evaluation script locally in dry-run mode:
```bash
# Test healthy state
python scripts/check_switch.py --now 1700000000 --commit-timestamp 1699900000 --dry-run

# Test warning state (15 days inactive)
python scripts/check_switch.py --now 1701300000 --commit-timestamp 1700000000 --dry-run

# Test triggered state (35 days inactive)
python scripts/check_switch.py --now 1703100000 --commit-timestamp 1700000000 --dry-run
```

---

## 8. Threat Model & Security Properties

| Property | Guarantee | Mechanism |
|---|---|---|
| **Zero-Knowledge Decryption** | Server never sees passphrases or decrypted payloads. | Web Crypto API (`crypto.subtle`) runs 100% in the recipient's browser. |
| **Tamper Resistance** | Any modification to ciphertext, IV, or salt is immediately detected. | AES-256-GCM 128-bit authentication tag verification. |
| **Brute-Force Resistance** | High computation cost for offline key derivation. | PBKDF2 with SHA-256 and 250,000 iterations per recipient. |
| **Repository Leak Safety** | If the private repository or `vault.json` is exposed, plaintexts remain confidential. | Strong AES-256-GCM encryption with per-recipient unique salts. |
| **KV Proof Secrecy** | Exposing Cloudflare KV does not expose passphrases or plaintexts. | Cloudflare KV stores one-way `SHA-256(passphrase + salt)` hashes. |
| **Autonomous Halting** | Recipients who acknowledge stop receiving nagging emails. | Client-side `/ack` writes `seen:<recipient_id>` to KV; evaluated by cron. |
| **Zero Running Costs** | 100% free tier. | GitHub Actions (free for private repos), Cloudflare Workers (100k req/day free), Cloudflare Pages (unlimited free). |

---

## License

MIT License. Designed and authored for sovereign privacy and automated security.
