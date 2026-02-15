# 🎙 Hebrew Live Subtitles v2

Real-time English subtitles for Hebrew audio in any Chrome tab, powered by Whisper AI.

```
hebrew-subtitles/
├── extension/
│   ├── manifest.json        ← Chrome MV3, tabCapture + scripting permissions
│   ├── popup.html / .js     ← Popup UI: start/stop, model, chunk, font, position
│   ├── background.js        ← Service worker: Web Audio tee → recorder → WebSocket
│   ├── contentScript.js     ← Subtitle overlay with configurable position & size
│   ├── overlay.css          ← Cinematic subtitle styles + drop-in animation
│   └── icons/               ← Extension icons
└── backend/
    ├── app.py               ← FastAPI + WebSocket /ws/translate
    ├── whisper_pipeline.py  ← faster-whisper / openai-whisper (Hebrew → English)
    ├── requirements.txt
    └── test_backend.py      ← Smoke-test script (local pipeline + WebSocket)
```

---

## What's new in v2

| Fix | Description |
|-----|-------------|
| **Audio not muted** | Web Audio API tee restores tab audio while recording |
| **Segment-level timing** | Each Whisper segment → individual subtitle message with accurate timestamps |
| **Cumulative timing** | Backend tracks stream time across chunks; no timestamp drift |
| **faster-whisper** | 4× faster on CPU, built-in VAD silence filter, int8 quantization |
| **Auto GPU detect** | Falls back to CPU automatically; set `WHISPER_GPU=true` to force CUDA |
| **WS reconnect** | Exponential-backoff auto-reconnect on disconnect |
| **Configurable UI** | Model size, chunk size, font size, subtitle position — all persisted |
| **Latency meter** | Popup shows real-time end-to-end latency with color-coded bar |
| **Smoke-test script** | `python test_backend.py` tests the pipeline without the browser |

---

## Backend Setup

### 1. Prerequisites: ffmpeg

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt update && sudo apt install ffmpeg

# Windows
winget install ffmpeg
```

### 2. Python environment

```bash
cd backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

> **CUDA GPU (optional, ~4× faster):**
> ```bash
> pip install torch --index-url https://download.pytorch.org/whl/cu121
> pip install faster-whisper
> ```

### 3. Model selection

Set `WHISPER_MODEL` before starting:

| Value    | Size    | CPU speed/chunk | Hebrew quality |
|----------|---------|-----------------|----------------|
| `tiny`   | 39 MB   | ~3s             | rough          |
| `base`   | 74 MB   | ~7s             | okay           |
| `small`  | 244 MB  | ~15s            | **good** ← default |
| `medium` | 769 MB  | ~35s            | great          |

```bash
export WHISPER_MODEL=small    # recommended starting point
export WHISPER_MODEL=medium   # if you have a GPU or patience
```

### 4. Start the backend

```bash
python app.py
# → http://localhost:8000/health  (check model loaded)
# → ws://localhost:8000/ws/translate  (WebSocket)
```

### 5. Smoke test (no browser needed)

```bash
# Test local pipeline with silence
python test_backend.py

# Test with a real WAV file
python test_backend.py /path/to/hebrew_audio.wav

# Test the running WebSocket server
python test_backend.py --ws
```

---

## Chrome Extension Setup

### 1. Load unpacked

1. `chrome://extensions` → enable **Developer mode**
2. **Load unpacked** → select the `extension/` folder
3. Extension icon appears in toolbar

### 2. Use it

1. Navigate to a tab with Hebrew audio (YouTube, streaming site, etc.)
2. Click the extension icon
3. Configure if needed (model must match what the backend has loaded)
4. Click **▶ Start**
5. English subtitles appear at the bottom within a few seconds

---

## Architecture

```
[Tab Audio]
    │
    ▼  chrome.tabCapture.capture()
[Raw MediaStream]
    │
    ├──► AudioContext.destination ──► 🔊 Speakers (tab audio RESTORED)
    │
    └──► MediaStreamDestination
              │
              ▼  MediaRecorder.start(chunkMs)
         [WebM/Opus chunks every 5s]
              │
              ▼  WebSocket binary frames
         [FastAPI /ws/translate]
              │
              ▼  pydub (ffmpeg) → 16kHz WAV
         [faster-whisper: task="translate", language="he"]
              │
              ▼  Per-segment JSON:
         { event:"subtitle", text:"...", start:s, end:s }
              │
              ▼  chrome.tabs.sendMessage()
         [Content Script → Overlay div]
              └─► Subtitle rendered at bottom of page ✓
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Tab audio is muted | Should be fixed in v2. If not, check that `AudioContext` is allowed (no browser flags blocking it). |
| "tabCapture failed" | Tab must be playing audio *before* you click Start. Reload the tab if needed. |
| "WS connection failed" | Start `python app.py` first. Check `http://localhost:8000/health`. |
| Subtitles are blank | Try `small` or `medium` model. Use `test_backend.py` to verify Whisper output. |
| Very slow (>20s/chunk) | Use `faster-whisper` (`pip install faster-whisper`), or add a GPU. |
| ffmpeg not found | Install ffmpeg and ensure it's in `$PATH` (`ffmpeg -version` should work). |
| No speech detected on silence | Expected — VAD filter suppresses silent/music-only chunks. |

---

## Environment Variables

| Variable | Default | Options |
|----------|---------|---------|
| `WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium`, `large-v3` |
| `WHISPER_BACKEND` | `faster` | `faster`, `openai` |
| `WHISPER_GPU` | `auto` | `auto`, `true`, `false` |
