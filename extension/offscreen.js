let mediaRecorder = null;
let audioCtx = null;
let sourceNode = null;
let gainNode = null;
let captureStream = null;

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
  captureStream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } }
  });
  console.log('[Offscreen] Got stream, tracks:', captureStream.getAudioTracks().length);

  audioCtx = new AudioContext();
  sourceNode = audioCtx.createMediaStreamSource(captureStream);
  gainNode = audioCtx.createGain();
  gainNode.gain.value = 1.0;
  const recDest = audioCtx.createMediaStreamDestination();

  sourceNode.connect(gainNode);
  gainNode.connect(recDest);
  // Also pass audio through to the tab so the user can still hear it
  gainNode.connect(audioCtx.destination);

  // Wait for actual audio data before starting the recorder
  await waitForAudioFlow(audioCtx, sourceNode);

  const mimeType = getSupportedMimeType();
  const chunkMs = (config.chunkSize || 5) * 1000;
  mediaRecorder = new MediaRecorder(recDest.stream, { mimeType });
  console.log('[Offscreen] MediaRecorder created on recDest.stream');
  let chunkIndex = 0;
  let chunkStartSec = 0;
  mediaRecorder.ondataavailable = async (e) => {
    if (!e.data || e.data.size < 512) return;
    const start = chunkStartSec;
    const end = start + chunkMs / 1000;
    chunkStartSec = end;
    const arrayBuffer = await e.data.arrayBuffer();
    chrome.runtime.sendMessage({
      type: 'AUDIO_CHUNK',
      chunk: arrayBuffer,
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

/**
 * Polls an AnalyserNode until non-silent audio is detected or timeout.
 * Returns once audio samples exceed the silence threshold.
 */
function waitForAudioFlow(ctx, source) {
  return new Promise((resolve) => {
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    source.connect(analyser);
    const buf = new Float32Array(analyser.fftSize);
    const SILENCE_THRESHOLD = 0.001;
    const POLL_INTERVAL = 50;
    const MAX_WAIT = 5000;
    let elapsed = 0;

    const poll = () => {
      analyser.getFloatTimeDomainData(buf);
      let maxVal = 0;
      for (let i = 0; i < buf.length; i++) {
        const abs = Math.abs(buf[i]);
        if (abs > maxVal) maxVal = abs;
      }
      if (maxVal > SILENCE_THRESHOLD) {
        console.log('[Offscreen] Audio detected (peak:', maxVal.toFixed(4), ') after', elapsed, 'ms');
        analyser.disconnect();
        resolve();
        return;
      }
      elapsed += POLL_INTERVAL;
      if (elapsed >= MAX_WAIT) {
        console.warn('[Offscreen] Audio wait timeout after', MAX_WAIT, 'ms, starting anyway');
        analyser.disconnect();
        resolve();
        return;
      }
      setTimeout(poll, POLL_INTERVAL);
    };
    poll();
  });
}

function stopCapture() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
  mediaRecorder = null;
  try { sourceNode?.disconnect(); } catch (_) {}
  try { gainNode?.disconnect(); } catch (_) {}
  try { audioCtx?.close(); } catch (_) {}
  sourceNode = gainNode = audioCtx = null;
  if (captureStream) { captureStream.getTracks().forEach(t => t.stop()); captureStream = null; }
}

function getSupportedMimeType() {
  for (const t of ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/ogg']) {
    if (MediaRecorder.isTypeSupported(t)) return t;
  }
  return '';
}