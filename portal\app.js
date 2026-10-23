/**
 * portal/app.js
 * =============
 * Architecture & interactions inspirées de Pacôme Pertant (pacomepertant.com / recent.design).
 * 
 * - Moteur audio Web Audio API (sub-bass boom, clicks de sélection, alertes)
 * - Curseur personnalisé inertiel avec étiquettes contextuelles
 * - Horloge temps réel Paris
 * - Double affichage (Galerie 3D avec inclinaison souris vs Index liste avec aperçu curseur)
 * - Déchiffrement SubtleCrypto (PBKDF2 250k + AES-GCM)
 * - Explosion sonore & visuelle lors du déverrouillage
 */

(function () {
  'use strict';

  // State
  let vaultData = null;
  let activeRecipient = null;
  let isSoundEnabled = true;
  let currentMode = 'spatial'; // 'spatial' | 'list'
  const sessionUnlocked = new Map(); // id -> { plaintext, proof }

  // Fallback Photos
  const DEFAULT_PHOTOS = [
    'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=1200&q=85',
    'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?auto=format&fit=crop&w=1200&q=85',
  ];

  // DOM Elements
  const customCursor = document.getElementById('customCursor');
  const cursorTrail = document.getElementById('cursorTrail');
  const cursorPreview = document.getElementById('cursorPreview');
  const cursorPreviewImg = document.getElementById('cursorPreviewImg');
  const realtimeClock = document.getElementById('realtimeClock');
  const soundToggleBtn = document.getElementById('soundToggleBtn');
  const modeSpatialBtn = document.getElementById('modeSpatialBtn');
  const modeListBtn = document.getElementById('modeListBtn');
  const spatialView = document.getElementById('spatialView');
  const listView = document.getElementById('listView');
  const listContainer = document.getElementById('listContainer');
  const vaultCountLabel = document.getElementById('vaultCountLabel');

  const gatewayStage = document.getElementById('gatewayStage');
  const closeGatewayBtn = document.getElementById('closeGatewayBtn');
  const fxStage = document.getElementById('fxStage');
  const unlockBox = document.getElementById('unlockBox');
  const documentStage = document.getElementById('documentStage');

  const dialogPhoto = document.getElementById('dialogPhoto');
  const dialogName = document.getElementById('dialogName');
  const dialogId = document.getElementById('dialogId');
  const dialogHint = document.getElementById('dialogHint');
  const codeField = document.getElementById('codeField');
  const btnDetonate = document.getElementById('btnDetonate');
  const dialogError = document.getElementById('dialogError');

  const docRecipientName = document.getElementById('docRecipientName');
  const docBody = document.getElementById('docBody');
  const docProofSpan = document.getElementById('docProofSpan');
  const docAckSpan = document.getElementById('docAckSpan');
  const copyDocBtn = document.getElementById('copyDocBtn');
  const downloadDocBtn = document.getElementById('downloadDocBtn');
  const closeDocBtn = document.getElementById('closeDocBtn');

  // --- Web Audio API Engine (Pacôme Pertant Sound Design) ---

  let audioCtx = null;
  function getAudioContext() {
    if (!audioCtx) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) audioCtx = new AudioContextClass();
    }
    if (audioCtx && audioCtx.state === 'suspended') {
      audioCtx.resume();
    }
    return audioCtx;
  }

  function playUiClick() {
    if (!isSoundEnabled) return;
    try {
      const ctx = getAudioContext();
      if (!ctx) return;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(1200, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(400, ctx.currentTime + 0.04);
      gain.gain.setValueAtTime(0.08, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.04);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.04);
    } catch {}
  }

  function playErrorBuzz() {
    if (!isSoundEnabled) return;
    try {
      const ctx = getAudioContext();
      if (!ctx) return;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(140, ctx.currentTime);
      osc.frequency.setValueAtTime(110, ctx.currentTime + 0.1);
      gain.gain.setValueAtTime(0.2, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.25);
    } catch {}
  }

  function playSonicBoom() {
    if (!isSoundEnabled) return;
    try {
      const ctx = getAudioContext();
      if (!ctx) return;

      // Deep sub-bass impact drop
      const subOsc = ctx.createOscillator();
      const subGain = ctx.createGain();
      subOsc.type = 'sine';
      subOsc.frequency.setValueAtTime(160, ctx.currentTime);
      subOsc.frequency.exponentialRampToValueAtTime(32, ctx.currentTime + 1.2);
      subGain.gain.setValueAtTime(0.9, ctx.currentTime);
      subGain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 1.2);
      subOsc.connect(subGain);
      subGain.connect(ctx.destination);
      subOsc.start();
      subOsc.stop(ctx.currentTime + 1.2);

      // Noise texture burst
      const bufferSize = ctx.sampleRate * 0.3;
      const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
      const data = buffer.getChannelData(0);
      for (let i = 0; i < bufferSize; i++) {
        data[i] = Math.random() * 2 - 1;
      }
      const noise = ctx.createBufferSource();
      noise.buffer = buffer;
      const noiseGain = ctx.createGain();
      noiseGain.gain.setValueAtTime(0.25, ctx.currentTime);
      noiseGain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
      noise.connect(noiseGain);
      noiseGain.connect(ctx.destination);
      noise.start();
      noise.stop(ctx.currentTime + 0.3);
    } catch {}
  }

  // --- Custom Inertia Cursor & Preview Track ---

  let mouseX = window.innerWidth / 2;
  let mouseY = window.innerHeight / 2;
  let trailX = mouseX;
  let trailY = mouseY;

  window.addEventListener('mousemove', (e) => {
    mouseX = e.clientX;
    mouseY = e.clientY;

    customCursor.style.left = `${mouseX}px`;
    customCursor.style.top = `${mouseY}px`;

    if (cursorPreview.classList.contains('visible')) {
      cursorPreview.style.left = `${mouseX + 160}px`;
      cursorPreview.style.top = `${mouseY}px`;
    }
  });

  function renderCursorTrail() {
    trailX += (mouseX - trailX) * 0.2;
    trailY += (mouseY - trailY) * 0.2;
    cursorTrail.style.left = `${trailX}px`;
    cursorTrail.style.top = `${trailY}px`;
    requestAnimationFrame(renderCursorTrail);
  }
  renderCursorTrail();

  function setCursorHover(isHovering, label = 'OPEN') {
    if (isHovering) {
      customCursor.classList.add('is-hovering');
      customCursor.textContent = label;
      cursorTrail.style.opacity = '0';
      playUiClick();
    } else {
      customCursor.classList.remove('is-hovering');
      customCursor.textContent = '';
      cursorTrail.style.opacity = '1';
    }
  }

  // --- Real-time Paris Clock ---

  function updateClock() {
    const now = new Date();
    const timeStr = now.toLocaleTimeString('fr-FR', {
      timeZone: 'Europe/Paris',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    realtimeClock.textContent = `PARIS ${timeStr} CET`;
  }
  setInterval(updateClock, 1000);
  updateClock();

  // --- Sound Toggle ---

  soundToggleBtn.addEventListener('click', () => {
    isSoundEnabled = !isSoundEnabled;
    soundToggleBtn.textContent = isSoundEnabled ? '[SOUND: ON]' : '[SOUND: OFF]';
    soundToggleBtn.classList.toggle('sound-on', isSoundEnabled);
    if (isSoundEnabled) playUiClick();
  });

  // --- SubtleCrypto (Client-side PBKDF2 + AES-GCM) ---

  function hexToUint8Array(hex) {
    if (hex.length % 2 !== 0) throw new Error('Hex length error');
    const arr = new Uint8Array(hex.length / 2);
    for (let i = 0; i < hex.length; i += 2) {
      arr[i / 2] = parseInt(hex.substr(i, 2), 16);
    }
    return arr;
  }

  function uint8ArrayToHex(arr) {
    return Array.from(arr).map(b => b.toString(16).padStart(2, '0')).join('');
  }

  async function deriveKey(passphrase, saltBytes, iterations = 250000) {
    const enc = new TextEncoder();
    const baseKey = await window.crypto.subtle.importKey(
      'raw',
      enc.encode(passphrase),
      { name: 'PBKDF2' },
      false,
      ['deriveKey']
    );

    return await window.crypto.subtle.deriveKey(
      {
        name: 'PBKDF2',
        salt: saltBytes,
        iterations: iterations,
        hash: 'SHA-256',
      },
      baseKey,
      { name: 'AES-GCM', length: 256 },
      false,
      ['decrypt']
    );
  }

  async function computeProof(passphrase, saltBytes) {
    const enc = new TextEncoder();
    const pBytes = enc.encode(passphrase);
    const combined = new Uint8Array(pBytes.length + saltBytes.length);
    combined.set(pBytes, 0);
    combined.set(saltBytes, pBytes.length);
    const hash = await window.crypto.subtle.digest('SHA-256', combined);
    return uint8ArrayToHex(new Uint8Array(hash));
  }

  async function decryptCiphertext(key, ivBytes, ctBytes) {
    const buf = await window.crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: ivBytes },
      key,
      ctBytes
    );
    return new TextDecoder('utf-8').decode(buf);
  }

  async function sendAck(workerUrl, recipientId, proof) {
    if (!workerUrl) {
      docAckSpan.textContent = 'MODE LOCAL (PAS DE WORKER CONFIGURÉ)';
      return;
    }
    docAckSpan.textContent = 'TRANSMISSION DE LA PREUVE À CLOUDFLARE KV...';
    try {
      const resp = await fetch(`${workerUrl.replace(/\/+$/, '')}/ack`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ recipient_id: recipientId, proof: proof }),
      });
      if (resp.ok) {
        docAckSpan.textContent = 'PREUVE ENREGISTRÉE DANS CLOUDFLARE KV // ALERTES STOPPÉES';
        docAckSpan.style.color = '#10b981';
      }
    } catch {
      docAckSpan.textContent = 'DÉCHIFFRÉ // INGRESS HORS LIGNE';
    }
  }

  // --- Render Layouts ---

  function renderViews() {
    spatialView.innerHTML = '';
    listContainer.innerHTML = '';

    const recipients = (vaultData && vaultData.recipients) ? vaultData.recipients : [];
    vaultCountLabel.textContent = `${recipients.length} PERSONNES ENREGISTRÉES`;

    recipients.forEach((r, idx) => {
      const isUnlocked = sessionUnlocked.has(r.id);
      const photoUrl = r.photo || DEFAULT_PHOTOS[idx % DEFAULT_PHOTOS.length];
      const numStr = String(idx + 1).padStart(2, '0');

      // 1. Spatial 3D Perspective Card
      const card = document.createElement('article');
      card.className = `spatial-card ${isUnlocked ? 'is-unlocked' : ''}`;
      card.setAttribute('data-id', r.id);
      card.innerHTML = `
        <img class="card-img-layer" src="${photoUrl}" alt="${r.name || r.id}">
        <div class="card-vignette"></div>
        <div class="card-ui-overlay">
          <div class="card-badge-row">
            <span class="index-pill">0${idx + 1} // ${r.id.toUpperCase()}</span>
            <span class="status-pill">${isUnlocked ? '&#x2714; DÉVERROUILLÉ' : 'SCELLÉ AES-256'}</span>
          </div>
          <div class="card-info-box">
            <h2>${r.name || r.id}</h2>
            ${r.hint ? `<div class="card-hint-row"><span>INDICE:</span> <span class="card-hint-pill">${r.hint}</span></div>` : ''}
          </div>
        </div>
      `;

      // 3D Tilt Effect on mouse movement (Pacôme Pertant physical effect)
      card.addEventListener('mousemove', (e) => {
        const rect = card.getBoundingClientRect();
        const x = e.clientX - rect.left - rect.width / 2;
        const y = e.clientY - rect.top - rect.height / 2;
        const rotX = -(y / rect.height) * 16;
        const rotY = (x / rect.width) * 16;
        card.style.transform = `perspective(1000px) rotateX(${rotX.toFixed(2)}deg) rotateY(${rotY.toFixed(2)}deg) scale(1.02)`;
      });

      card.addEventListener('mouseleave', () => {
        card.style.transform = 'perspective(1000px) rotateX(0deg) rotateY(0deg) scale(1)';
        setCursorHover(false);
      });

      card.addEventListener('mouseenter', () => {
        setCursorHover(true, isUnlocked ? 'READ' : 'UNLOCK');
      });

      card.addEventListener('click', () => openGateway(r, photoUrl, idx));
      spatialView.appendChild(card);

      // 2. Editorial Index List Row
      const row = document.createElement('li');
      row.className = 'list-row';
      row.innerHTML = `
        <div class="list-row-left">
          <span class="list-row-num">${numStr}</span>
          <span class="list-row-name">${r.name || r.id}</span>
        </div>
        <div class="list-row-right">
          ${isUnlocked ? '[DÉVERROUILLÉ]' : '[SCELLÉ // AES-256-GCM]'}
        </div>
      `;

      row.addEventListener('mouseenter', () => {
        setCursorHover(true, 'UNLOCK');
        cursorPreviewImg.src = photoUrl;
        cursorPreview.classList.add('visible');
      });

      row.addEventListener('mouseleave', () => {
        setCursorHover(false);
        cursorPreview.classList.remove('visible');
      });

      row.addEventListener('click', () => {
        cursorPreview.classList.remove('visible');
        openGateway(r, photoUrl, idx);
      });

      listContainer.appendChild(row);
    });
  }

  // --- View Mode Toggle ---

  modeSpatialBtn.addEventListener('click', () => {
    currentMode = 'spatial';
    modeSpatialBtn.classList.add('active');
    modeListBtn.classList.remove('active');
    spatialView.style.display = 'grid';
    listView.style.display = 'none';
    playUiClick();
  });

  modeListBtn.addEventListener('click', () => {
    currentMode = 'list';
    modeListBtn.classList.add('active');
    modeSpatialBtn.classList.remove('active');
    spatialView.style.display = 'none';
    listView.style.display = 'block';
    playUiClick();
  });

  // --- Gateway / Explosion / Reveal ---

  function openGateway(recipient, photoUrl, idx) {
    activeRecipient = recipient;
    dialogError.style.display = 'none';
    codeField.value = '';

    dialogPhoto.src = photoUrl;
    dialogName.textContent = recipient.name || recipient.id;
    dialogId.textContent = `ARCHIVE CONFIDENTIELLE #${String(idx + 1).padStart(2, '0')} // ${recipient.id.toUpperCase()}`;

    if (recipient.hint) {
      dialogHint.innerHTML = `INDICE DU CODE : <strong>${recipient.hint}</strong>`;
      dialogHint.style.display = 'block';
    } else {
      dialogHint.style.display = 'none';
    }

    fxStage.classList.remove('exploding');

    if (sessionUnlocked.has(recipient.id)) {
      unlockBox.style.display = 'none';
      const data = sessionUnlocked.get(recipient.id);
      showDocument(data.plaintext, data.proof);
    } else {
      unlockBox.style.display = 'block';
      documentStage.classList.remove('is-visible');
    }

    gatewayStage.classList.add('is-active');
    gatewayStage.setAttribute('aria-hidden', 'false');

    if (!sessionUnlocked.has(recipient.id)) {
      setTimeout(() => codeField.focus(), 150);
    }
  }

  function closeGateway() {
    gatewayStage.classList.remove('is-active');
    gatewayStage.setAttribute('aria-hidden', 'true');
    activeRecipient = null;
    codeField.value = '';
    fxStage.classList.remove('exploding');
  }

  async function handleUnlock() {
    if (!activeRecipient) return;

    dialogError.style.display = 'none';
    const passphrase = codeField.value;

    if (!passphrase) {
      dialogError.textContent = 'VEUILLEZ SAISIR LE CODE';
      dialogError.style.display = 'block';
      playErrorBuzz();
      codeField.focus();
      return;
    }

    btnDetonate.disabled = true;
    btnDetonate.textContent = 'DÉCHIFFREMENT SUBTLECRYPTO...';

    await new Promise(r => setTimeout(r, 40));

    try {
      const saltBytes = hexToUint8Array(activeRecipient.salt);
      const ivBytes = hexToUint8Array(activeRecipient.iv);
      const ctBytes = hexToUint8Array(activeRecipient.ciphertext);
      const iterations = vaultData.pbkdf2_iterations || 250000;

      // 1. PBKDF2 derive key
      const key = await deriveKey(passphrase, saltBytes, iterations);

      // 2. Decrypt AES-256-GCM
      const plaintext = await decryptCiphertext(key, ivBytes, ctBytes);

      // 3. Compute Proof SHA-256
      const proofHex = await computeProof(passphrase, saltBytes);

      sessionUnlocked.set(activeRecipient.id, { plaintext, proof: proofHex });

      // 4. BOOM : SONIC BOOM + SHOCKWAVE EXPLOSION !
      playSonicBoom();
      fxStage.classList.add('exploding');

      unlockBox.style.transition = 'transform 0.4s ease, opacity 0.4s ease';
      unlockBox.style.transform = 'scale(1.2)';
      unlockBox.style.opacity = '0';

      // 5. Expand & reveal document
      setTimeout(() => {
        unlockBox.style.display = 'none';
        unlockBox.style.transform = '';
        unlockBox.style.opacity = '';
        showDocument(plaintext, proofHex);
        renderViews(); // Updates card to unlocked status

        // Dispatch background acknowledgment
        const workerUrl = vaultData.worker_url || '';
        sendAck(workerUrl, activeRecipient.id, proofHex);
      }, 550);

    } catch (err) {
      console.error(err);
      playErrorBuzz();
      dialogError.textContent = 'CODE INCORRECT // ÉCHEC DE DÉCHIFFREMENT';
      dialogError.style.display = 'block';
      codeField.select();
    } finally {
      btnDetonate.disabled = false;
      btnDetonate.textContent = 'DÉVERROUILLER L\'ARCHIVE';
    }
  }

  function showDocument(plaintext, proofHex) {
    docRecipientName.textContent = `DOCUMENT // ${activeRecipient.name || activeRecipient.id}`;
    docBody.textContent = plaintext;
    docProofSpan.textContent = proofHex;
    documentStage.classList.add('is-visible');
  }

  // --- Initializer ---

  async function init() {
    try {
      const resp = await fetch('vault.json');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      vaultData = await resp.json();
      renderViews();

      // Check ?recipient=alice
      const params = new URLSearchParams(window.location.search);
      const targetId = params.get('recipient') || window.location.hash.replace('#', '');
      if (targetId && vaultData && vaultData.recipients) {
        const foundIdx = vaultData.recipients.findIndex(r => r.id.toLowerCase() === targetId.toLowerCase());
        if (foundIdx !== -1) {
          const rec = vaultData.recipients[foundIdx];
          const photo = rec.photo || DEFAULT_PHOTOS[foundIdx % DEFAULT_PHOTOS.length];
          openGateway(rec, photo, foundIdx);
        }
      }
    } catch (err) {
      console.error('Erreur chargement vault.json:', err);
    }
  }

  // --- Listeners ---

  closeGatewayBtn.addEventListener('click', closeGateway);
  closeDocBtn.addEventListener('click', closeGateway);

  gatewayStage.addEventListener('click', (e) => {
    if (e.target === gatewayStage) closeGateway();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && gatewayStage.classList.contains('is-active')) {
      closeGateway();
    }
  });

  btnDetonate.addEventListener('click', handleUnlock);
  codeField.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') handleUnlock();
  });

  copyDocBtn.addEventListener('click', () => {
    if (!docBody.textContent) return;
    navigator.clipboard.writeText(docBody.textContent).then(() => {
      const orig = copyDocBtn.innerHTML;
      copyDocBtn.innerHTML = '<span>&#x2714; COPIÉ</span>';
      setTimeout(() => { copyDocBtn.innerHTML = orig; }, 2000);
    });
  });

  downloadDocBtn.addEventListener('click', () => {
    if (!docBody.textContent) return;
    const blob = new Blob([docBody.textContent], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `archive-${activeRecipient ? activeRecipient.id : 'confidentielle'}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  });

  init();
})();
