"""
app.py — FastAPI WebSocket server for Hebrew → English live subtitles
Endpoint: ws://localhost:8000/ws/translate

Wire protocol (extension → backend):
  Frame 1 (text):   { "event": "chunk", "index": N, "start": s, "end": s, "mimeType": "..." }
  Frame 2 (binary): <raw audio bytes>

Wire protocol (backend → extension):
  { "event": "subtitle", "text": "...", "start": s, "end": s }
  { "event": "ack",      "index": N }
  { "event": "error",    "message": "..." }

Key design: cumulative_time is tracked per-connection so that Whisper's
per-chunk segment timestamps are converted to global stream time.
"""

import asyncio
import json
import logging
import traceback
from contextlib import asynccontextmanager
from functools import partial
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from whisper_pipeline import WhisperPipeline

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("app")

# ── Startup: load model once ──────────────────────────────────────────────────
pipeline: Optional[WhisperPipeline] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline
    log.info("Loading Whisper model…")
    pipeline = WhisperPipeline()
    log.info("Whisper model ready ✓")
    yield

app = FastAPI(title="Hebrew Live Subtitles", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── WebSocket endpoint ─────────────────────────────────────────────────────────
@app.websocket("/ws/translate")
async def ws_translate(ws: WebSocket):
    await ws.accept()
    log.info("Client connected: %s", ws.client)

    # Per-connection state
    pending_meta: Optional[dict] = None
    cumulative_time: float = 0.0        # seconds of audio processed so far

    try:
        while True:
            raw = await ws.receive()

            # ── Disconnect ────────────────────────────────────────────────────
            if raw.get("type") == "websocket.disconnect":
                break

            # ── Text frame → chunk metadata ───────────────────────────────────
            if raw.get("text"):
                try:
                    pending_meta = json.loads(raw["text"])
                    await ws.send_text(json.dumps({
                        "event": "ack",
                        "index": pending_meta.get("index", -1),
                    }))
                except json.JSONDecodeError as e:
                    await ws.send_text(json.dumps({"event": "error", "message": f"Bad JSON: {e}"}))
                continue

            # ── Binary frame → audio ──────────────────────────────────────────
            if not raw.get("bytes"):
                continue

            audio_bytes = raw["bytes"]
            meta        = pending_meta or {}
            pending_meta = None

            chunk_start  = meta.get("start", cumulative_time)
            chunk_end    = meta.get("end",   chunk_start + 5.0)
            mime_type    = meta.get("mimeType", "audio/webm")
            index        = meta.get("index", -1)
            chunk_dur    = chunk_end - chunk_start

            log.info("Chunk #%d | %.1f–%.1fs | %d bytes", index, chunk_start, chunk_end, len(audio_bytes))

            # Run Whisper off the event-loop thread
            loop = asyncio.get_event_loop()
            try:
                segments = await loop.run_in_executor(
                    None,
                    partial(pipeline.transcribe, audio_bytes, mime_type, chunk_start),
                )
            except Exception as exc:
                log.error("Whisper error: %s", exc)
                traceback.print_exc()
                await ws.send_text(json.dumps({"event": "error", "message": str(exc)}))
                cumulative_time += chunk_dur
                continue

            # Emit one subtitle message per Whisper segment
            for seg in segments:
                if seg.text:
                    log.info("  Subtitle [%.1f–%.1fs]: %s", seg.start, seg.end, seg.text)
                    await ws.send_text(json.dumps({
                        "event": "subtitle",
                        "text":  seg.text,
                        "start": round(seg.start, 2),
                        "end":   round(seg.end,   2),
                    }))

            cumulative_time += chunk_dur

    except WebSocketDisconnect:
        log.info("Client disconnected: %s", ws.client)
    except Exception as exc:
        log.error("Unhandled error: %s", exc)
        traceback.print_exc()
        try:
            await ws.send_text(json.dumps({"event": "error", "message": str(exc)}))
        except Exception:
            pass


# ── Health / smoke-test endpoints ──────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model" : pipeline.model_name if pipeline else "loading",
    }


@app.post("/test-whisper")
async def test_whisper(request: Request):
    """
    Smoke-test endpoint. POST a WAV/WebM file as raw body.
    curl -X POST --data-binary @sample.wav http://localhost:8000/test-whisper
    """
    from fastapi import Request  # already imported at top
    body = await request.body()
    if not body:
        return {"error": "no audio body"}
    segs = await asyncio.get_event_loop().run_in_executor(
        None, partial(pipeline.transcribe, body, "audio/wav", 0.0)
    )
    return {"segments": [{"text": s.text, "start": s.start, "end": s.end} for s in segs]}


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False, log_level="info")
