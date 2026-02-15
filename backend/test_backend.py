#!/usr/bin/env python3
"""
test_backend.py — Smoke-test script for the Hebrew subtitles backend.

Usage:
  python test_backend.py                          # generate silence + transcribe
  python test_backend.py path/to/audio.wav       # transcribe a real file
  python test_backend.py --ws                    # test the WebSocket endpoint
"""

import asyncio
import io
import json
import struct
import sys
import time
import wave


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_silence_wav(duration_s: float = 5.0, sample_rate: int = 16000) -> bytes:
    """Generate a silent WAV file (for testing the pipeline without real audio)."""
    n_samples = int(duration_s * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack('<' + 'h' * n_samples, *([0] * n_samples)))
    return buf.getvalue()


def test_pipeline_local(audio_path: str = None):
    """Load model locally and run transcription — no HTTP needed."""
    print("── Local pipeline test ──────────────────────────────────")
    sys.path.insert(0, '.')
    from whisper_pipeline import WhisperPipeline

    pipeline = WhisperPipeline()

    if audio_path:
        with open(audio_path, 'rb') as f:
            audio_bytes = f.read()
        mime = 'audio/wav' if audio_path.endswith('.wav') else 'audio/webm'
    else:
        print("No file given — using 5 s of silence (expect no segments)")
        audio_bytes = make_silence_wav(5.0)
        mime = 'audio/wav'

    print(f"Audio: {len(audio_bytes):,} bytes | MIME: {mime}")
    t0 = time.time()
    segments = pipeline.transcribe(audio_bytes, mime, time_offset=0.0)
    elapsed  = time.time() - t0

    print(f"Whisper finished in {elapsed:.2f}s → {len(segments)} segment(s)")
    for s in segments:
        print(f"  [{s.start:.1f}s–{s.end:.1f}s] {s.text}")

    if not segments:
        print("  (no speech detected — silence suppressed correctly ✓)")
    print()


async def test_websocket(url: str = "ws://localhost:8000/ws/translate"):
    """Connect to the running backend and send a silent WAV chunk."""
    try:
        import websockets
    except ImportError:
        print("pip install websockets")
        return

    print(f"── WebSocket test → {url}")
    audio = make_silence_wav(5.0)
    mime  = "audio/wav"

    async with websockets.connect(url) as ws:
        # Send metadata
        await ws.send(json.dumps({
            "event": "chunk", "index": 0, "start": 0.0, "end": 5.0, "mimeType": mime,
        }))
        ack = json.loads(await ws.recv())
        print(f"  ACK: {ack}")

        # Send audio
        await ws.send(audio)

        # Wait for subtitle or error (up to 30 s)
        print("  Waiting for response…")
        try:
            msg_raw = await asyncio.wait_for(ws.recv(), timeout=30)
            msg = json.loads(msg_raw)
            print(f"  Response: {msg}")
        except asyncio.TimeoutError:
            print("  Timeout — no response in 30 s (backend may still be processing)")

    print()


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--ws" in args:
        url = next((a for a in args if a.startswith("ws://")), "ws://localhost:8000/ws/translate")
        asyncio.run(test_websocket(url))
    else:
        audio_path = args[0] if args and not args[0].startswith("--") else None
        test_pipeline_local(audio_path)
