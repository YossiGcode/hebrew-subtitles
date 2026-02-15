// popup.js — syncs settings to chrome.storage.sync, drives start/stop/status

const btnStart    = document.getElementById('btn-start');
const btnStop     = document.getElementById('btn-stop');
const dot         = document.getElementById('dot');
const statusText  = document.getElementById('status-text');
const previewEl   = document.getElementById('preview-text');
const chunkInput  = document.getElementById('chunk-size');
const chunkVal    = document.getElementById('chunk-val');
const fontInput   = document.getElementById('font-size');
const fontVal     = document.getElementById('font-val');
const modelSel    = document.getElementById('model-size');
const posSel      = document.getElementById('overlay-pos');
const wsUrlInput  = document.getElementById('ws-url');
const latBar      = document.getElementById('latency-bar');
const latLabel    = document.getElementById('latency-label');

let subtitleReceivedAt = 0;

// ── Restore persisted settings ───────────────────────────────────────────────
chrome.storage.sync.get(
  ['chunkSize', 'fontSize', 'modelSize', 'overlayPos', 'wsUrl', 'lastSubtitle'],
  (d) => {
    if (d.chunkSize)    { chunkInput.value = d.chunkSize;  chunkVal.textContent  = d.chunkSize  + 's'; }
    if (d.fontSize)     { fontInput.value  = d.fontSize;   fontVal.textContent   = d.fontSize   + 'px'; }
    if (d.modelSize)    { modelSel.value   = d.modelSize; }
    if (d.overlayPos)   { posSel.value     = d.overlayPos; }
    if (d.wsUrl)        { wsUrlInput.value = d.wsUrl; }
    if (d.lastSubtitle) { previewEl.textContent = d.lastSubtitle; }
  }
);

// Check if already running
chrome.storage.local.get(['running'], ({ running }) => {
  if (running) setRunningUI(true);
});

// ── Live range label updates ─────────────────────────────────────────────────
chunkInput.addEventListener('input', () => { chunkVal.textContent = chunkInput.value + 's'; });
fontInput.addEventListener('input',  () => {
  fontVal.textContent = fontInput.value + 'px';
  broadcastDisplaySettings();
});
posSel.addEventListener('change', broadcastDisplaySettings);

function broadcastDisplaySettings() {
  const settings = { fontSize: parseInt(fontInput.value), overlayPos: posSel.value };
  chrome.storage.sync.set(settings);
  chrome.runtime.sendMessage({ type: 'UPDATE_DISPLAY', ...settings }).catch(() => {});
}

// ── Start ────────────────────────────────────────────────────────────────────
btnStart.addEventListener('click', () => {
  const cfg = {
    chunkSize  : parseInt(chunkInput.value, 10),
    modelSize  : modelSel.value,
    overlayPos : posSel.value,
    fontSize   : parseInt(fontInput.value, 10),
    wsUrl      : wsUrlInput.value.trim() || 'ws://localhost:8000/ws/translate',
  };

  // Persist all settings
  chrome.storage.sync.set({
    chunkSize : cfg.chunkSize,
    modelSize : cfg.modelSize,
    overlayPos: cfg.overlayPos,
    fontSize  : cfg.fontSize,
    wsUrl     : cfg.wsUrl,
  });

  setStatus('connecting…', 'active');

  chrome.runtime.sendMessage({ type: 'START_CAPTURE', config: cfg }, (resp) => {
    if (chrome.runtime.lastError || !resp?.ok) {
      setStatus('error: ' + (resp?.error ?? 'check backend'), 'error');
      setRunningUI(false);
    }
  });

  setRunningUI(true);
});

// ── Stop ─────────────────────────────────────────────────────────────────────
btnStop.addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'STOP_CAPTURE' });
  setRunningUI(false);
  setStatus('stopped', '');
  setLatency(null);
});

// ── Listen for messages from background ──────────────────────────────────────
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'STATUS_UPDATE') {
    setStatus(msg.text, msg.state);
  }
  if (msg.type === 'SUBTITLE_PREVIEW') {
    previewEl.textContent = msg.text;
    // Latency: time from chunk-end to subtitle received
    const now = Date.now();
    if (subtitleReceivedAt > 0) {
      const ms = now - subtitleReceivedAt;
      setLatency(ms);
    }
    subtitleReceivedAt = now;
  }
});

// ── Helpers ──────────────────────────────────────────────────────────────────
function setRunningUI(running) {
  btnStart.disabled = running;
  btnStop.disabled  = !running;
  // Disable settings while running
  [chunkInput, modelSel, wsUrlInput].forEach(el => { el.disabled = running; });
}

function setStatus(text, state) {
  statusText.textContent = text;
  dot.className = 'dot' + (state ? ' ' + state : '');
}

function setLatency(ms) {
  if (ms === null) {
    latLabel.textContent = '— ms';
    latBar.style.width   = '0';
    return;
  }
  latLabel.textContent = ms + ' ms';
  // Bar: green at <3000 ms, orange at <8000, red beyond
  const pct = Math.min(100, (ms / 10000) * 100);
  latBar.style.width      = pct + '%';
  latBar.style.background = ms < 3000 ? '#4af0a8' : ms < 8000 ? '#f59e0b' : '#f43f5e';
}
