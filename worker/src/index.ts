/**
 * worker/src/index.ts
 * ====================
 * Contrôleur Cloudflare Worker avec la D.A. ✲ (TypeScript)
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
}

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { "Content-Type": "application/json", ...CORS_HEADERS },
  });
}

function htmlResponse(html: string, status = 200): Response {
  return new Response(html, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8", ...CORS_HEADERS },
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

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    if (url.pathname === "/ping") {
      const token = extractToken(request, url);

      if (!env.HEARTBEAT_SECRET || token !== env.HEARTBEAT_SECRET) {
        return jsonResponse({ status: "error", message: "jeton invalide." }, 401);
      }

      let elapsedSeconds = 0;
      if (env.DMS_KV) {
        const raw = await env.DMS_KV.get("last_heartbeat");
        if (raw) {
          try {
            const parsed = JSON.parse(raw);
            elapsedSeconds = Math.max(0, Math.floor(Date.now() / 1000) - parsed.timestamp);
          } catch {}
        }
      }

      const isSyncAction = url.searchParams.get("sync") === "1" || request.method === "POST";
      let justSynced = false;
      let githubTriggered = false;

      if (isSyncAction) {
        const nowUnix = Math.floor(Date.now() / 1000);
        const isoDate = new Date().toISOString();

        if (env.DMS_KV) {
          await env.DMS_KV.put("last_heartbeat", JSON.stringify({ timestamp: nowUnix, iso: isoDate }));
        }

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
                "User-Agent": "Sync-Worker",
                "Content-Type": "application/json",
              },
              body: JSON.stringify({ ref: branchRef, inputs: { reason: "Routine sync" } }),
            });
            githubTriggered = ghRes.status >= 200 && ghRes.status < 300;
          } catch {}
        }

        justSynced = true;
        elapsedSeconds = 0;
      }

      const days = Math.floor(elapsedSeconds / 86400);
      const hours = Math.floor((elapsedSeconds % 86400) / 3600);
      const minutes = Math.floor((elapsedSeconds % 3600) / 60);

      let elapsedText = "";
      if (elapsedSeconds < 60) {
        elapsedText = "à l'instant";
      } else if (days > 0) {
        elapsedText = `il y a ${days} jour${days > 1 ? "s" : ""}`;
        if (hours > 0) elapsedText += ` et ${hours}h`;
      } else if (hours > 0) {
        elapsedText = `il y a ${hours} heure${hours > 1 ? "s" : ""}`;
      } else {
        elapsedText = `il y a ${minutes} minute${minutes > 1 ? "s" : ""}`;
      }

      const acceptHeader = request.headers.get("Accept") || "";
      if (acceptHeader.includes("text/html")) {
        return htmlResponse(`<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>✲</title>
<style>
:root{--bg:#efeee9;--fg:#111;--mut:#777;--ac:#ff4d1c;box-sizing:border-box}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#111110;--fg:#efeee9;--mut:#888}}
:root[data-theme="dark"]{--bg:#111110;--fg:#efeee9;--mut:#888}
*{box-sizing:border-box;margin:0}
html,body{height:100%}
body{background:var(--bg);color:var(--fg);font:16px/1.55 Georgia,"Times New Roman",serif;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:24px;padding:24px;text-align:center}
h1{font-size:clamp(34px,8vw,84px);font-weight:400;letter-spacing:-.03em;line-height:1;font-style:italic}
.sub{color:var(--mut);font-size:14px}
.time{font-size:24px;font-style:italic;margin:6px 0}
a.btn{border:1px solid var(--fg);background:none;color:inherit;border-radius:99px;padding:8px 22px;cursor:pointer;font-size:14px;text-decoration:none;transition:background .2s,color .2s}
a.btn:hover{background:var(--fg);color:var(--bg)}
.ok{color:var(--ac);font-size:14px}
</style>
</head>
<body>
<h1>✲</h1>
<p class="sub">synchronisation de session</p>
<p class="time">${elapsedText}</p>
${justSynced ? '<p class="ok">synchronisé à l\'instant.</p>' : ''}
<a href="/ping?token=${token}&sync=1" class="btn">${justSynced ? 're-synchroniser' : 'synchroniser'}</a>
</body>
</html>`);
      }

      return jsonResponse({
        status: "ok",
        elapsed_seconds: elapsedSeconds,
        elapsed_text: elapsedText,
        just_synced: justSynced,
        github_triggered: githubTriggered,
      });
    }

    if (url.pathname === "/ack" && request.method === "POST") {
      let body: any;
      try { body = await request.json(); } catch {
        return jsonResponse({ status: "error", message: "json invalide" }, 400);
      }
      const { recipient_id, proof } = body;
      if (!recipient_id || !proof) return jsonResponse({ status: "error" }, 400);

      const normalizedId = recipient_id.trim().toLowerCase();
      const normalizedProof = proof.trim().toLowerCase();

      if (env.DMS_KV) {
        const expectedProof = await env.DMS_KV.get(`proof:${normalizedId}`);
        if (expectedProof && expectedProof.trim().toLowerCase() !== normalizedProof) {
          return jsonResponse({ status: "error", message: "preuve invalide" }, 403);
        }
        await env.DMS_KV.put(`seen:${normalizedId}`, JSON.stringify({
          seen: true,
          recipient_id: normalizedId,
          proof: normalizedProof,
          timestamp: Math.floor(Date.now() / 1000),
          iso: new Date().toISOString(),
        }));
      }
      return jsonResponse({ status: "ok", recipient_id: normalizedId });
    }

    if (url.pathname === "/status" && request.method === "GET") {
      const token = extractToken(request, url);
      if (!env.STATUS_TOKEN || token !== env.STATUS_TOKEN) {
        return jsonResponse({ status: "error", message: "unauthorized" }, 401);
      }
      let lastHeartbeat = null;
      const recipients: Record<string, any> = {};

      if (env.DMS_KV) {
        const raw = await env.DMS_KV.get("last_heartbeat");
        if (raw) {
          try { lastHeartbeat = JSON.parse(raw); } catch {}
        }
        const seenList = await env.DMS_KV.list({ prefix: "seen:" });
        for (const key of seenList.keys) {
          const id = key.name.replace("seen:", "");
          const val = await env.DMS_KV.get(key.name);
          if (val) {
            try { recipients[id] = JSON.parse(val); } catch { recipients[id] = { seen: true }; }
          }
        }
      }

      return jsonResponse({ status: "ok", last_heartbeat: lastHeartbeat, seen_recipients: recipients });
    }

    return jsonResponse({ service: "✲", status: "online" });
  },
};
