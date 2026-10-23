/**
 * worker/src/index.ts
 * ====================
 * Cloudflare Worker: Heartbeat & Ingress Controller for Dead Man's Switch
 * 
 * Endpoints:
 * - GET/POST /ping?token=<SECRET>: Receives owner heartbeat, triggers GitHub Actions workflow_dispatch.
 * - POST /ack: Receives recipient decryption proof, validates against KV, stores seen:<recipient_id>.
 * - GET /status: Protected route for GitHub Actions to query recipient seen states.
 * - POST /seed: Protected route to upload expected cryptographic proofs into KV.
 */

export interface Env {
  DMS_KV: KVNamespace;
  HEARTBEAT_SECRET?: string;
  STATUS_TOKEN?: string;
  GITHUB_PAT?: string;
  GITHUB_OWNER?: string;
  GITHUB_REPO?: string;
  GITHUB_WORKFLOW?: string;
  GITHUB_BRANCH?: string;
  ALLOWED_ORIGIN?: string;
}

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: {
      "Content-Type": "application/json",
      ...CORS_HEADERS,
    },
  });
}

function htmlResponse(html: string, status = 200): Response {
  return new Response(html, {
    status,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      ...CORS_HEADERS,
    },
  });
}

function extractToken(request: Request, url: URL): string | null {
  const queryToken = url.searchParams.get("token");
  if (queryToken) return queryToken;

  const authHeader = request.headers.get("Authorization");
  if (authHeader && authHeader.startsWith("Bearer ")) {
    return authHeader.substring(7).trim();
  }
  return null;
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    // Handle CORS Preflight
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: CORS_HEADERS,
      });
    }

    // --- Route 1: Heartbeat Proof of Life (/ping) ---
    if (url.pathname === "/ping") {
      const token = extractToken(request, url);

      if (!env.HEARTBEAT_SECRET || token !== env.HEARTBEAT_SECRET) {
        return jsonResponse({ status: "error", message: "Unauthorized: Invalid or missing heartbeat token." }, 401);
      }

      const nowUnix = Math.floor(Date.now() / 1000);
      const isoDate = new Date().toISOString();

      // Record heartbeat in Cloudflare KV
      await env.DMS_KV.put(
        "last_heartbeat",
        JSON.stringify({ timestamp: nowUnix, iso: isoDate })
      );

      // Trigger GitHub Actions workflow_dispatch if PAT and repo configured
      let githubTriggered = false;
      let githubStatus = "skipped (no GitHub PAT configured)";

      if (env.GITHUB_PAT && env.GITHUB_OWNER && env.GITHUB_REPO) {
        const workflowFile = env.GITHUB_WORKFLOW || "heartbeat.yml";
        const branchRef = env.GITHUB_BRANCH || "main";
        const ghApiUrl = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/${workflowFile}/dispatches`;

        try {
          const ghRes = await fetch(ghApiUrl, {
            method: "POST",
            headers: {
              "Authorization": `Bearer ${env.GITHUB_PAT}`,
              "Accept": "application/vnd.github.v3+json",
              "User-Agent": "Cloudflare-Worker-DMS-Ingress",
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ ref: branchRef }),
          });

          if (ghRes.status === 204 || ghRes.status === 200 || ghRes.status === 201) {
            githubTriggered = true;
            githubStatus = "success (GitHub Actions workflow_dispatch triggered)";
          } else {
            const errText = await ghRes.text();
            githubStatus = `failed (HTTP ${ghRes.status}: ${errText})`;
          }
        } catch (err: any) {
          githubStatus = `error: ${err.message || String(err)}`;
        }
      }

      // Check if browser requested HTML
      const acceptHeader = request.headers.get("Accept") || "";
      if (acceptHeader.includes("text/html")) {
        return htmlResponse(`
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Heartbeat Recorded</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0a0e17; color: #f0f4f8; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
    .card { background: #131b2e; border: 1px solid #10b981; border-radius: 12px; padding: 2rem; max-width: 480px; width: 90%; text-align: center; box-shadow: 0 10px 30px rgba(16, 185, 129, 0.2); }
    .icon { font-size: 3rem; margin-bottom: 1rem; color: #10b981; }
    h1 { font-size: 1.5rem; margin-bottom: 0.5rem; color: #ffffff; }
    p { color: #94a3b8; font-size: 0.95rem; line-height: 1.5; margin-bottom: 1rem; }
    .meta { font-family: monospace; font-size: 0.8rem; background: #070a10; padding: 0.75rem; border-radius: 6px; color: #38bdf8; text-align: left; word-break: break-all; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">&#x2714;</div>
    <h1>Heartbeat Recorded</h1>
    <p>Your proof of life has been successfully registered. The Dead Man's Switch timer has been reset.</p>
    <div class="meta">
      Timestamp: ${isoDate}<br>
      GitHub Dispatch: ${githubStatus}
    </div>
  </div>
</body>
</html>
        `);
      }

      return jsonResponse({
        status: "ok",
        message: "Heartbeat successfully recorded",
        timestamp: nowUnix,
        iso: isoDate,
        github: {
          triggered: githubTriggered,
          status: githubStatus,
        },
      });
    }

    // --- Route 2: Recipient Decryption Acknowledgment (/ack) ---
    if (url.pathname === "/ack" && request.method === "POST") {
      let body: any;
      try {
        body = await request.json();
      } catch {
        return jsonResponse({ status: "error", message: "Malformed JSON payload" }, 400);
      }

      const { recipient_id, proof } = body;
      if (!recipient_id || typeof recipient_id !== "string" || !proof || typeof proof !== "string") {
        return jsonResponse({ status: "error", message: "Missing recipient_id or proof in request" }, 400);
      }

      const normalizedId = recipient_id.trim().toLowerCase();
      const normalizedProof = proof.trim().toLowerCase();

      // Verify proof if expected proof is seeded in KV
      const expectedProof = await env.DMS_KV.get(`proof:${normalizedId}`);
      if (expectedProof) {
        if (expectedProof.trim().toLowerCase() !== normalizedProof) {
          return jsonResponse({
            status: "error",
            message: "Cryptographic proof verification failed. Expected proof does not match.",
          }, 403);
        }
      }

      const nowUnix = Math.floor(Date.now() / 1000);
      const isoDate = new Date().toISOString();

      const seenRecord = {
        seen: true,
        recipient_id: normalizedId,
        proof: normalizedProof,
        timestamp: nowUnix,
        iso: isoDate,
      };

      await env.DMS_KV.put(`seen:${normalizedId}`, JSON.stringify(seenRecord));

      return jsonResponse({
        status: "ok",
        message: `Decryption acknowledged and recorded for ${normalizedId}`,
        timestamp: nowUnix,
      });
    }

    // --- Route 3: Status Inspection Query (/status) ---
    if (url.pathname === "/status" && request.method === "GET") {
      const token = extractToken(request, url);

      if (!env.STATUS_TOKEN || token !== env.STATUS_TOKEN) {
        return jsonResponse({ status: "error", message: "Unauthorized: Invalid or missing status token." }, 401);
      }

      // Read last heartbeat
      const lastHeartbeatRaw = await env.DMS_KV.get("last_heartbeat");
      const lastHeartbeat = lastHeartbeatRaw ? JSON.parse(lastHeartbeatRaw) : null;

      // Scan all seen records
      const seenList = await env.DMS_KV.list({ prefix: "seen:" });
      const recipients: Record<string, any> = {};

      for (const key of seenList.keys) {
        const id = key.name.replace("seen:", "");
        const raw = await env.DMS_KV.get(key.name);
        if (raw) {
          try {
            recipients[id] = JSON.parse(raw);
          } catch {
            recipients[id] = { seen: true, raw };
          }
        }
      }

      return jsonResponse({
        status: "ok",
        timestamp: Math.floor(Date.now() / 1000),
        last_heartbeat: lastHeartbeat,
        seen_recipients: recipients,
      });
    }

    // --- Route 4: Bulk Seed Expected Proofs (/seed) ---
    if (url.pathname === "/seed" && request.method === "POST") {
      const token = extractToken(request, url);

      if (!env.STATUS_TOKEN || token !== env.STATUS_TOKEN) {
        return jsonResponse({ status: "error", message: "Unauthorized: Invalid status/admin token." }, 401);
      }

      let body: any;
      try {
        body = await request.json();
      } catch {
        return jsonResponse({ status: "error", message: "Malformed JSON" }, 400);
      }

      const proofs = body.proofs;
      if (!proofs || typeof proofs !== "object") {
        return jsonResponse({ status: "error", message: "Missing proofs object { [recipient_id]: proof_hex }" }, 400);
      }

      let count = 0;
      for (const [id, proof] of Object.entries(proofs)) {
        if (typeof proof === "string") {
          await env.DMS_KV.put(`proof:${id.trim().toLowerCase()}`, proof.trim().toLowerCase());
          count++;
        }
      }

      return jsonResponse({
        status: "ok",
        message: `Successfully seeded ${count} cryptographic proofs into KV`,
      });
    }

    // Default route
    if (url.pathname === "/") {
      return jsonResponse({
        service: "Dead Man's Switch - Heartbeat & Ingress Controller",
        version: "1.0.0",
        routes: ["/ping", "/ack", "/status", "/seed"],
      });
    }

    return jsonResponse({ status: "error", message: "Not found" }, 404);
  },
};
