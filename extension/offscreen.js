let mediaRecorder = null;
let captureStream = null;
let liveAudio = null; // New: Handle playback

// New state for header splicing
let webmHeader = null; 

chrome.runtime.sendMessage({ type: 'OFFSCREEN_READY' }).catch(() => {});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'START_CAPTURE') {
    startCapture(msg.streamId, msg.config)
      .then(() => sendResponse({ success: true }))
      .catch((err) => sendResponse({ success: false, error: err.message }));
    return true;
  }
  if (msg.type === 'STOP_CAPTURE') {
    stopCapture();
    sendResponse({ success: true });
  }
});

async function startCapture(streamId, config) {
  console.log('[Offscreen] Starting capture');
  
  // Reset state
  webmHeader = null;
  
  captureStream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } }
  });
  console.log('[Offscreen] Got stream, tracks:', captureStream.getAudioTracks().length);

  // ── FIX: Play audio so user can hear it ──────────────────
  liveAudio = new Audio();
  liveAudio.srcObject = captureStream;
  liveAudio.play();
  // ─────────────────────────────────────────────────────────

  // Direct stream capture
  const mimeType = getSupportedMimeType();
  const chunkMs = (config.chunkSize || 5) * 1000;
  
  try {
    mediaRecorder = new MediaRecorder(captureStream, { mimeType });
  } catch (e) {
    console.warn('[Offscreen] Direct MediaRecorder failed, trying default settings', e);
    mediaRecorder = new MediaRecorder(captureStream);
  }

  console.log('[Offscreen] MediaRecorder created');
  
  let chunkIndex = 0;
  let chunkStartSec = 0;
  
  mediaRecorder.ondataavailable = async (e) => {
    if (!e.data || e.data.size < 1) return;
    
    const start = chunkStartSec;
    const end = start + chunkMs / 1000;
    chunkStartSec = end;
    
    const arrayBuffer = await e.data.arrayBuffer();
    
    // ── Logic: WebM Header Splicing ──────────────────────────
    let dataToSend = arrayBuffer;
    
    if (chunkIndex === 0) {
      const clusterOffset = findClusterOffset(arrayBuffer);
      if (clusterOffset > 0) {
        webmHeader = arrayBuffer.slice(0, clusterOffset);
        console.log(`[Offscreen] Captured WebM Header: ${webmHeader.byteLength} bytes`);
      }
    } else {
      if (webmHeader) {
        const combined = new Uint8Array(webmHeader.byteLength + arrayBuffer.byteLength);
        combined.set(new Uint8Array(webmHeader), 0);
        combined.set(new Uint8Array(arrayBuffer), webmHeader.byteLength);
        dataToSend = combined.buffer;
      }
    }

    // ── Logic: Convert to Base64 ─────────────────────────────
    const base64String = arrayBufferToBase64(dataToSend);

    chrome.runtime.sendMessage({
      type: 'AUDIO_CHUNK',
      chunk: base64String,
      metadata: { index: chunkIndex++, start: start, end: end, mimeType: mimeType }
    }).catch(() => {});
  };
  
  mediaRecorder.onerror = (e) => {
    console.error('[Offscreen] Recorder error:', e);
    chrome.runtime.sendMessage({ type: 'CAPTURE_ERROR', error: e.error?.message || 'error' }).catch(() => {});
  };
  
  mediaRecorder.start(chunkMs);
  console.log('[Offscreen] Recording started, chunk interval:', chunkMs, 'ms');
}

function arrayBufferToBase64(buffer) {
  let binary = '';
  const bytes = new Uint8Array(buffer);
  const len = bytes.byteLength;
  for (let i = 0; i < len; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

function findClusterOffset(buffer) {
  const view = new Uint8Array(buffer);
  const len = view.length;
  for (let i = 0; i < len - 3; i++) {
    if (view[i] === 0x1F && view[i+1] === 0x43 && view[i+2] === 0xB6 && view[i+3] === 0x75) {
      return i;
    }
  }
  return -1;
}

function stopCapture() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
  mediaRecorder = null;
  webmHeader = null;
  
  // Stop playback
  if (liveAudio) {
    liveAudio.pause();
    liveAudio.srcObject = null;
    liveAudio = null;
  }
  
  if (captureStream) { captureStream.getTracks().forEach(t => t.stop()); captureStream = null; }
}

function getSupportedMimeType() {
  for (const t of ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/ogg']) {
    if (MediaRecorder.isTypeSupported(t)) return t;
  }
  return '';
}