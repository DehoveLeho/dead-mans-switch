/**
 * worker/src/index.js
 * ====================
 * Contrôleur Cloudflare Worker : Heartbeat épuré à l'extrême (aucun symbole, aucun texte superflu).
 */

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { "Content-Type": "application/json", ...CORS_HEADERS },
  });
}

function htmlResponse(html, status = 200) {
  return new Response(html, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8", ...CORS_HEADERS },
  });
}

function extractToken(request, url) {
  const queryToken = url.searchParams.get("token");
  if (queryToken) return queryToken;

  const authHeader = request.headers.get("Authorization");
  if (authHeader && authHeader.startsWith("Bearer ")) {
    return authHeader.substring(7).trim();
  }
  return null;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    // --- Route 1: Heartbeat épuré ---
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

      const isSyncAction = request.method === "POST";
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
<title></title>
<style>
:root{--bg:#efeee9;--fg:#111;--mut:#777;--ac:#ff4d1c;box-sizing:border-box}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#111110;--fg:#efeee9;--mut:#888}}
:root[data-theme="dark"]{--bg:#111110;--fg:#efeee9;--mut:#888}
*{box-sizing:border-box;margin:0}
html,body{height:100%}
body{background:var(--bg);color:var(--fg);font:16px/1.55 Georgia,"Times New Roman",serif;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:20px;padding:24px;text-align:center}
.time{font-size:clamp(22px,5vw,36px);font-style:italic;margin-bottom:8px}
button.btn{border:1px solid var(--fg);background:none;color:inherit;border-radius:99px;padding:8px 24px;cursor:pointer;font-size:15px;text-decoration:none;transition:background .2s,color .2s;font-family:inherit;-webkit-appearance:none;outline:none}
button.btn:hover{background:var(--fg);color:var(--bg)}
.ok{color:var(--ac);font-size:14px}
</style>
</head>
<body>
<p class="time">${elapsedText}</p>
${justSynced ? '<p class="ok">synchronisé.</p>' : ''}
<form method="POST" action="/ping?token=${token}">
  <button type="submit" class="btn">${justSynced ? 're-synchroniser' : 'synchroniser'}</button>
</form>
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

    // --- Route 2: Accusé de lecture (/ack) ---
    if (url.pathname === "/ack" && request.method === "POST") {
      let body;
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

    // --- Route 3: Statut inspecté par GitHub Actions (/status) ---
    if (url.pathname === "/status" && request.method === "GET") {
      const token = extractToken(request, url);
      if (!env.STATUS_TOKEN || token !== env.STATUS_TOKEN) {
        return jsonResponse({ status: "error", message: "unauthorized" }, 401);
      }
      let lastHeartbeat = null;
      const recipients = {};

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

    // --- Route 4: Portfolio Telemetry & Discord Dispatch (/telemetry) ---
    if (url.pathname === "/telemetry" && request.method === "POST") {
      let data = {};
      const contentType = request.headers.get("Content-Type") || "";
      if (contentType.includes("application/json")) {
        try { data = await request.json(); } catch {}
      } else {
        try {
          const form = await request.formData();
          for (const [k, v] of form.entries()) {
            data[k] = v;
          }
        } catch {}
      }

      // Save visit in KV for analytics
      if (env.DMS_KV) {
        const id = data.Session_ID || ("sess_" + Date.now());
        const key = `visit:${Date.now()}:${id.substring(0, 16)}`;
        if (ctx && ctx.waitUntil) {
          ctx.waitUntil(env.DMS_KV.put(key, JSON.stringify(data), { expirationTtl: 86400 * 30 }));
        } else {
          await env.DMS_KV.put(key, JSON.stringify(data), { expirationTtl: 86400 * 30 });
        }
      }

      const discordWebhook = env.DISCORD_WEBHOOK_URL || "https://discord.com/api/webhooks/1565468128513761354/s8-TYuGfWTHwp3QabdlFiu9azh0k6tvbIjEn2wJn2mXM9ksuOm5J7DZsZ4uMS37qdvLU";
      if (discordWebhook) {
        const subject = data._subject || "Visite Portfolio";
        const isCv = subject.includes("CV") || (data.CV_Telecharge && data.CV_Telecharge.includes("OUI"));
        const isArrival = (data.Statut && data.Statut.includes("NOUVELLE")) || subject.includes("Arrivée");

        const color = isCv ? 0xf1c40f : (isArrival ? 0x2ecc71 : 0x3498db);

        const fields = [];
        if (data.name) fields.push({ name: "👤 Expéditeur", value: String(data.name), inline: true });
        if (data.email) fields.push({ name: "✉️ Email", value: String(data.email), inline: true });
        if (data.message) fields.push({ name: "💬 Message", value: String(data.message).substring(0, 1024), inline: false });

        const importantKeys = [
          { key: "Duree_Totale", label: "⏱️ Durée totale", inline: true },
          { key: "CV_Telecharge", label: "🎯 CV téléchargé", inline: true },
          { key: "Profondeur_Scroll_Max", label: "📜 Scroll max", inline: true },
          { key: "Geolocalisation", label: "📍 Localisation", inline: false },
          { key: "Provenance_Referrer", label: "🔗 Provenance", inline: true },
          { key: "Appareil_et_OS", label: "💻 Appareil / OS", inline: true },
          { key: "Temps_Passe_Par_Section", label: "⏳ Temps par section", inline: false },
          { key: "Historique_Des_Clics", label: "🖱️ Clics liens", inline: false },
          { key: "Interactions_Tortues_Hero", label: "🐢 Tortues interactives", inline: true },
          { key: "Textes_Copies_Par_Visiteur", label: "📋 Textes copiés", inline: false },
        ];

        for (const item of importantKeys) {
          if (data[item.key]) {
            fields.push({
              name: item.label,
              value: String(data[item.key]).substring(0, 1024),
              inline: !!item.inline,
            });
          }
        }

        const embed = {
          title: subject,
          color: color,
          fields: fields.slice(0, 25),
          footer: { text: `Session: ${data.Session_ID || "inconnue"}` },
          timestamp: new Date().toISOString(),
        };

        const postPromise = fetch(discordWebhook, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ embeds: [embed] }),
        }).catch(() => {});

        if (ctx && ctx.waitUntil) {
          ctx.waitUntil(postPromise);
        } else {
          await postPromise;
        }
      }

      return jsonResponse({ status: "ok", dispatched: true });
    }

    return jsonResponse({ service: "sync", status: "online" });
  },
};
