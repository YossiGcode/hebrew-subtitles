"""
whisper_pipeline.py — Hebrew audio → English subtitles via Whisper

Two backends supported (auto-selected):
  1. faster-whisper   (CTranslate2, 4× faster on CPU, lower RAM)  ← preferred
  2. openai-whisper   (original PyTorch implementation)            ← fallback

Set WHISPER_BACKEND=openai to force the original.
Set WHISPER_MODEL to control model size (default: "small").

Subtitle timing:
  Each call to `transcribe()` returns a list of Segment dicts:
    { "text": str, "start": float, "end": float }
  The caller supplies `time_offset` (cumulative seconds) so that segment
  timestamps are expressed in global stream time, not per-chunk time.

Hebrew model options (set via WHISPER_MODEL env var):
  "tiny"    — 39 MB,  fastest, rough quality
  "base"    — 74 MB,  fast, okay for clear speech
  "small"   — 244 MB, good balance             ← default (much better for Hebrew)
  "medium"  — 769 MB, great accuracy, slow on CPU
  "large-v3"— 1.5 GB, best accuracy, GPU recommended

ivrit-ai fine-tuned models (Hebrew-specific, better for Israeli TV):
  Set WHISPER_MODEL=ivrit-ai/whisper-v2-d3-e3 to use the fine-tuned model.
  These are hosted on HuggingFace and require `transformers` package.
  NOTE: ivrit-ai models do transcription (Hebrew text), not translation.
  For translation, use standard whisper models with task="translate".
"""

import io
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from typing import List, Optional

log = logging.getLogger("whisper_pipeline")

# FFMPEG_PATH / FFMPEG_BIN: directory (e.g. C:\\ffmpeg\\bin) or path to ffmpeg.exe.
# pydub uses ffmpeg/ffprobe; if not in PATH, set this so we point pydub at the right binaries.
_ffmpeg_configured = False


def _find_ffmpeg_candidates() -> List[str]:
    """Return candidate directories/paths to check for ffmpeg.exe (Windows-friendly)."""
    candidates = []
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(script_dir)
    candidates.append(os.path.join(script_dir, "ffmpeg", "bin"))
    for name in ("C:\\ffmpeg\\bin", "C:\\ffmpeg", "C:\\Program Files\\ffmpeg\\bin"):
        if os.path.isdir(name):
            candidates.append(name)
    return candidates


def _configure_ffmpeg_path() -> None:
    """Point pydub at ffmpeg/ffprobe. Uses FFMPEG_PATH/FFMPEG_BIN if set, else searches common locations. Idempotent."""
    global _ffmpeg_configured
    if _ffmpeg_configured:
        return
    base = os.getenv("FFMPEG_PATH") or os.getenv("FFMPEG_BIN")
    if base and base.strip():
        base = os.path.normpath(base.strip())
        if base.lower().endswith((".exe", ".bat")):
            ffmpeg_path = base
            ffprobe_dir = os.path.dirname(base)
        else:
            ffprobe_dir = base
            ffmpeg_path = os.path.join(base, "ffmpeg.exe")
    else:
        # Not in env: check PATH first, then common locations
        if shutil.which("ffmpeg"):
            _ffmpeg_configured = True
            return
        ffmpeg_path = None
        ffprobe_dir = None
        for d in _find_ffmpeg_candidates():
            exe = os.path.join(d, "ffmpeg.exe")
            if os.path.isfile(exe):
                ffmpeg_path = exe
                ffprobe_dir = d
                break
        if not ffmpeg_path or not ffprobe_dir:
            _ffmpeg_configured = True
            return
    ffprobe_path = os.path.join(ffprobe_dir, "ffprobe.exe")
    if not os.path.isfile(ffmpeg_path):
        if base:
            log.warning("FFMPEG_PATH set but ffmpeg not found at %s", ffmpeg_path)
        _ffmpeg_configured = True
        return
    import pydub.utils as pydub_utils
    from pydub import AudioSegment
    AudioSegment.converter = ffmpeg_path
    _original_get_prober = pydub_utils.get_prober_name

    def _get_prober_name():
        if os.path.isfile(ffprobe_path):
            return ffprobe_path
        return _original_get_prober()

    pydub_utils.get_prober_name = _get_prober_name
    log.info("Using ffmpeg at %s", ffmpeg_path)
    _ffmpeg_configured = True

BACKEND   = os.getenv("WHISPER_BACKEND", "faster").lower()   # "faster" | "openai"
MODEL_ID  = os.getenv("WHISPER_MODEL",   "small")
USE_GPU   = os.getenv("WHISPER_GPU",     "auto").lower()       # "auto" | "true" | "false"


@dataclass
class Segment:
    text:  str
    start: float   # seconds from start of this audio chunk
    end:   float


class WhisperPipeline:
    """
    Unified interface for faster-whisper and openai-whisper.
    Call transcribe(audio_bytes, mime_type, time_offset) to get List[Segment].
    """

    def __init__(self):
        self.model_name = MODEL_ID
        self._device    = self._pick_device()
        self._backend   = BACKEND

        log.info("Device: %s | Backend: %s | Model: %s", self._device, BACKEND, MODEL_ID)

        if BACKEND == "faster":
            self._load_faster_whisper()
        else:
            self._load_openai_whisper()

    # ── Loading ────────────────────────────────────────────────────────────────

    def _load_faster_whisper(self):
        try:
            from faster_whisper import WhisperModel
            compute = "float16" if self._device == "cuda" else "int8"
            log.info("Loading faster-whisper/%s on %s (%s)…", MODEL_ID, self._device, compute)
            t0 = time.time()
            self._model   = WhisperModel(MODEL_ID, device=self._device, compute_type=compute)
            self._backend = "faster"
            log.info("faster-whisper ready in %.1fs ✓", time.time() - t0)
        except ImportError:
            log.warning("faster-whisper not installed — falling back to openai-whisper")
            self._backend = "openai"
            self._load_openai_whisper()

    def _load_openai_whisper(self):
        import whisper
        log.info("Loading openai-whisper/%s on %s…", MODEL_ID, self._device)
        t0 = time.time()
        self._model = whisper.load_model(MODEL_ID, device=self._device)
        log.info("openai-whisper ready in %.1fs ✓", time.time() - t0)

    # ── Public API ─────────────────────────────────────────────────────────────

    def transcribe(
        self,
        audio_bytes: bytes,
        mime_type:   str   = "audio/webm",
        time_offset: float = 0.0,
    ) -> List[Segment]:
        """
        Translate Hebrew audio chunk → list of English subtitle segments.
        Segment timestamps are offset by `time_offset` (cumulative stream time).

        Returns [] if audio is silent or no speech detected.
        """
        if not audio_bytes or len(audio_bytes) < 512:
            return []

        wav_bytes = self._decode_to_wav(audio_bytes, mime_type)
        if wav_bytes is None:
            return []

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            tmp = f.name

        try:
            t0 = time.time()
            if self._backend == "faster":
                segments = self._run_faster(tmp, time_offset)
            else:
                segments = self._run_openai(tmp, time_offset)
            log.info("Whisper %.2fs → %d segment(s)", time.time() - t0, len(segments))
            return segments
        finally:
            try: os.unlink(tmp)
            except OSError: pass

    # ── Backends ───────────────────────────────────────────────────────────────

    def _run_faster(self, wav_path: str, offset: float) -> List[Segment]:
        """faster-whisper returns a generator of Segment objects."""
        segs, info = self._model.transcribe(
            wav_path,
            task               = "translate",
            language           = "he",
            beam_size          = 5,
            vad_filter         = True,          # built-in silence filter
            vad_parameters     = dict(min_silence_duration_ms=500),
            no_speech_threshold= 0.55,
            temperature        = 0.0,           # greedy, deterministic
        )
        results = []
        for s in segs:
            text = s.text.strip()
            if text and text not in ("[BLANK_AUDIO]", "[Music]", "(Music)"):
                results.append(Segment(
                    text  = text,
                    start = offset + s.start,
                    end   = offset + s.end,
                ))
        return results

    def _run_openai(self, wav_path: str, offset: float) -> List[Segment]:
        """openai-whisper returns a dict with a 'segments' list."""
        import whisper
        result = self._model.transcribe(
            wav_path,
            task                         = "translate",
            language                     = "he",
            fp16                         = (self._device == "cuda"),
            verbose                      = False,
            no_speech_threshold          = 0.55,
            logprob_threshold            = -1.0,
            compression_ratio_threshold  = 2.4,
            temperature                  = 0.0,
        )
        results = []
        for s in result.get("segments", []):
            text = s["text"].strip()
            if text:
                results.append(Segment(
                    text  = text,
                    start = offset + s["start"],
                    end   = offset + s["end"],
                ))
        return results

    # ── Audio decoding ─────────────────────────────────────────────────────────

    def _decode_to_wav(self, audio_bytes: bytes, mime_type: str) -> Optional[bytes]:
        """
        Decode WebM/OGG/Opus → 16 kHz mono WAV using pydub + ffmpeg.
        Whisper expects 16 kHz mono PCM.
        """
        _configure_ffmpeg_path()
        try:
            from pydub import AudioSegment as AS
            fmt   = _mime_to_fmt(mime_type)
            audio = AS.from_file(io.BytesIO(audio_bytes), format=fmt)
            audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
            buf   = io.BytesIO()
            audio.export(buf, format="wav")
            return buf.getvalue()
        except Exception as e:
            log.error("Audio decode failed (%s): %s", mime_type, e)
            return None

    # ── Device selection ───────────────────────────────────────────────────────

    @staticmethod
    def _pick_device() -> str:
        setting = USE_GPU
        if setting == "true":
            return "cuda"
        if setting == "false":
            return "cpu"
        # auto
        try:
            import torch
            if torch.cuda.is_available():
                log.info("CUDA GPU detected ✓")
                return "cuda"
        except ImportError:
            pass
        return "cpu"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mime_to_fmt(mime_type: str) -> str:
    table = {
        "audio/webm":            "webm",
        "audio/webm;codecs=opus":"webm",
        "audio/ogg":             "ogg",
        "audio/ogg;codecs=opus": "ogg",
        "audio/mp4":             "mp4",
        "audio/mpeg":            "mp3",
        "audio/wav":             "wav",
    }
    base = mime_type.split(";")[0].strip().lower()
    return table.get(mime_type.lower(), table.get(base, "webm"))
