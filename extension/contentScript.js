// contentScript.js — injects subtitle overlay and handles display settings
// Guard: run only once per page
(function () {
  if (document.getElementById('hls-overlay-root')) return;

  // ── Build overlay DOM ─────────────────────────────────────────────────────
  const root  = document.createElement('div');
  root.id = 'hls-overlay-root';

  const inner = document.createElement('div');
  inner.id = 'hls-subtitle-text';

  root.appendChild(inner);
  document.documentElement.appendChild(root);

  // ── Load Noto Sans for crisp subtitle rendering ───────────────────────────
  const link = document.createElement('link');
  link.rel  = 'stylesheet';
  link.href = 'https://fonts.googleapis.com/css2?family=Noto+Sans:wght@700&display=swap';
  document.head?.appendChild(link);

  // ── State ─────────────────────────────────────────────────────────────────
  let hideTimer = null;

  // ── Apply display settings (from popup or storage) ────────────────────────
  function applySettings({ fontSize, overlayPos }) {
    if (fontSize) {
      inner.style.fontSize = fontSize + 'px';
    }
    if (overlayPos) {
      root.classList.remove('hls-pos-top', 'hls-pos-center');
      if (overlayPos === 'top')    root.classList.add('hls-pos-top');
      if (overlayPos === 'center') root.classList.add('hls-pos-center');
    }
  }

  // Restore settings from storage on inject
  chrome.storage.sync.get(['fontSize', 'overlayPos'], applySettings);

  // ── Show a subtitle ───────────────────────────────────────────────────────
  function showSubtitle(text) {
    // Reset animation by briefly removing the class
    root.classList.remove('hls-visible');
    // Force reflow so animation re-triggers
    void root.offsetWidth;

    inner.textContent = text;
    root.classList.add('hls-visible');

    clearTimeout(hideTimer);
    // Keep visible proportional to text length; minimum 2.5 s
    const durationMs = Math.max(2500, text.length * 55);
    hideTimer = setTimeout(() => root.classList.remove('hls-visible'), durationMs);
  }

  function clearSubtitles() {
    clearTimeout(hideTimer);
    root.classList.remove('hls-visible');
    inner.textContent = '';
  }

  // ── Message listener ──────────────────────────────────────────────────────
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === 'SUBTITLE' && msg.text?.trim()) {
      showSubtitle(msg.text.trim());
    }
    if (msg.type === 'CLEAR_SUBTITLES') {
      clearSubtitles();
    }
    if (msg.type === 'UPDATE_DISPLAY') {
      applySettings(msg);
    }
  });

})();
