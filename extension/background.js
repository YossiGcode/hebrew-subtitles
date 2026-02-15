let socket = null;
let currentTabId = null;
let config = {};
let wsReconnectTimer = null;
let isRunning = false;

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'START_CAPTURE') {
    startCapture(msg.config).then(() => sendResponse({ ok: true })).catch((e) => { console.error('[BG]', e); sendResponse({ ok: false, error: e.message }); });
    return true;
  }
  if (msg.type === 'STOP_CAPTURE') { stopCapture('manual'); sendResponse({ ok: true }); }
  if (msg.type === 'AUDIO_CHUNK') { handleAudioChunk(msg.chunk, msg.metadata); }
  if (msg.type === 'CAPTURE_ERROR') { notifyStatus('capture error: ' + msg.error, 'error'); }
});

chrome.tabs.onRemoved.addListener((id) => { if (id === currentTabId) stopCapture('tab closed'); });
chrome.tabs.onUpdated.addListener((id, info) => { if (id === currentTabId && info.status === 'loading') stopCapture('tab navigated'); });

async function startCapture(cfg) {
  if (isRunning) stopCapture('restart');
  config = cfg;
  isRunning = true;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error('No active tab found');
  currentTabId = tab.id;
  await chrome.scripting.executeScript({ target: { tabId: currentTabId }, files: ['contentScript.js'] }).catch(() => {});
  await openWebSocket(cfg.wsUrl);
  console.log('[BG] Getting stream ID');
  const streamId = await new Promise((resolve, reject) => {
    chrome.tabCapture.getMediaStreamId({ targetTabId: currentTabId }, (id) => {
      if (chrome.runtime.lastError || !id) reject(new Error(chrome.runtime.lastError?.message || 'failed'));
      else resolve(id);
    });
  });
  console.log('[BG] Got stream ID:', streamId);
  await setupOffscreenDocument();
  await new Promise(resolve => setTimeout(resolve, 200));
  const result = await chrome.runtime.sendMessage({ type: 'START_CAPTURE', streamId: streamId, config: cfg });
  if (!result || !result.success) throw new Error('Offscreen failed: ' + (result?.error || 'no response'));
  console.log('[BG] Capture started');
  notifyStatus('live', 'active');
  chrome.storage.local.set({ running: true });
}

function stopCapture(reason) {
  console.log('[BG] stop:', reason);
  isRunning = false;
  clearTimeout(wsReconnectTimer);
  chrome.runtime.sendMessage({ type: 'STOP_CAPTURE' }).catch(() => {});
  if (socket) { socket.close(); socket = null; }
  chrome.storage.local.set({ running: false });
  if (currentTabId) chrome.tabs.sendMessage(currentTabId, { type: 'CLEAR_SUBTITLES' }).catch(() => {});
}

async function setupOffscreenDocument() {
  const existing = await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'], documentUrls: [chrome.runtime.getURL('offscreen.html')] });
  if (existing.length > 0) { console.log('[BG] Offscreen exists'); return; }
  console.log('[BG] Creating offscreen');
  await chrome.offscreen.createDocument({ url: 'offscreen.html', reasons: ['USER_MEDIA'], justification: 'Audio capture' });
  console.log('[BG] Offscreen created');
}

// ── FIXED FUNCTION (Base64 Decoding) ─────────────────────────────────────────
function handleAudioChunk(chunkBase64, metadata) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  
  // 1. Decode Base64 back to Binary
  // We use the binary string from atob() and write it into a Uint8Array
  const binaryString = atob(chunkBase64);
  const len = binaryString.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }

  // 2. Send JSON Metadata
  socket.send(JSON.stringify({ 
    event: 'chunk', 
    index: metadata.index, 
    start: metadata.start, 
    end: metadata.end, 
    mimeType: metadata.mimeType 
  }));

  // 3. Send Binary Audio
  socket.send(bytes.buffer);
}
// ─────────────────────────────────────────────────────────────────────────────

function openWebSocket(url, attempt = 0) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    socket = ws;
    const t = setTimeout(() => { ws.close(); reject(new Error('timeout')); }, 7000);
    ws.onopen = () => { clearTimeout(t); notifyStatus('connected', 'active'); resolve(ws); };
    ws.onerror = () => { clearTimeout(t); reject(new Error('failed')); };
    ws.onclose = (evt) => {
      if (!isRunning) return;
      const delay = Math.min(2000 * 2 ** attempt, 12000);
      notifyStatus('reconnecting in ' + (delay/1000) + 's', 'error');
      wsReconnectTimer = setTimeout(() => { if (!isRunning) return; openWebSocket(url, attempt + 1).then(() => notifyStatus('reconnected', 'active')).catch(() => {}); }, delay);
    };
    ws.onmessage = handleServerMessage;
  });
}

function handleServerMessage(event) {
  let msg;
  try { msg = JSON.parse(event.data); } catch (e) { return; }
  if (msg.event === 'subtitle' && msg.text && msg.text.trim()) {
    chrome.tabs.sendMessage(currentTabId, { type: 'SUBTITLE', text: msg.text, start: msg.start, end: msg.end }).catch(() => {});
    chrome.runtime.sendMessage({ type: 'SUBTITLE_PREVIEW', text: msg.text }).catch(() => {});
    chrome.storage.local.set({ lastSubtitle: msg.text });
  }
  if (msg.event === 'error') notifyStatus('backend: ' + msg.message, 'error');
}

function notifyStatus(text, state) {
  chrome.runtime.sendMessage({ type: 'STATUS_UPDATE', text: text, state: state }).catch(() => {});
}