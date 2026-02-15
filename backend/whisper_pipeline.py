"""
whisper_pipeline.py — Hebrew audio → English subtitles via Whisper

Optimized for speed: 
- Uses in-memory processing (BytesIO) for faster-whisper to avoid disk I/O latency.
- Falls back to temp files only for openai-whisper.
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

_ffmpeg_configured = False

def _find_ffmpeg_candidates() -> List[str]:
    candidates = []
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(script_dir)
    candidates.append(os.path.join(script_dir, "ffmpeg", "bin"))
    for name in ("C:\\ffmpeg\\bin", "C:\\ffmpeg", "C:\\Program Files\\ffmpeg\\bin"):
        if os.path.isdir(name):
            candidates.append(name)
    return candidates

def _configure_ffmpeg_path() -> None:
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

BACKEND   = os.getenv("WHISPER_BACKEND", "faster").lower()
MODEL_ID  = os.getenv("WHISPER_MODEL",   "small")
USE_GPU   = os.getenv("WHISPER_GPU",     "auto").lower()

@dataclass
class Segment:
    text:  str
    start: float
    end:   float

class WhisperPipeline:
    def __init__(self):
        self.model_name = MODEL_ID
        self._device    = self._pick_device()
        self._backend   = BACKEND

        log.info("Device: %s | Backend: %s | Model: %s", self._device, BACKEND, MODEL_ID)

        if BACKEND == "faster":
            self._load_faster_whisper()
        else:
            self._load_openai_whisper()

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
        import whisper # type: ignore
        log.info("Loading openai-whisper/%s on %s…", MODEL_ID, self._device)
        t0 = time.time()
        self._model = whisper.load_model(MODEL_ID, device=self._device)
        log.info("openai-whisper ready in %.1fs ✓", time.time() - t0)

    def transcribe(
        self,
        audio_bytes: bytes,
        mime_type:   str   = "audio/webm",
        time_offset: float = 0.0,
    ) -> List[Segment]:
        if not audio_bytes or len(audio_bytes) < 512:
            return []

        wav_bytes = self._decode_to_wav(audio_bytes, mime_type)
        if wav_bytes is None:
            return []

        t0 = time.time()
        
        # ── OPTIMIZATION: RAM-only processing for faster-whisper ──
        if self._backend == "faster":
            # Pass file-like object directly to faster-whisper (No Disk I/O)
            file_obj = io.BytesIO(wav_bytes)
            segments = self._run_faster(file_obj, time_offset)
        else:
            # openai-whisper requires a real file path
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(wav_bytes)
                tmp = f.name
            try:
                segments = self._run_openai(tmp, time_offset)
            finally:
                try: os.unlink(tmp)
                except OSError: pass

        log.info("Whisper %.2fs → %d segment(s)", time.time() - t0, len(segments))
        return segments

    def _run_faster(self, audio_input, offset: float) -> List[Segment]:
        # audio_input can be a file path OR a binary file-like object
        segs, info = self._model.transcribe(
            audio_input,
            task               = "translate",
            language           = "he",
            beam_size          = 5,
            vad_filter         = True,
            vad_parameters     = dict(min_silence_duration_ms=500),
            no_speech_threshold= 0.55,
            temperature        = 0.0,
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
        import whisper # type: ignore
        result = self._model.transcribe(
            wav_path,
            task                         = "translate",
            language                     = "he",
            fp16                         = (self._device == "cuda"),
            verbose                      = False,
            no_speech_threshold          = 0.55,
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

    def _decode_to_wav(self, audio_bytes: bytes, mime_type: str) -> Optional[bytes]:
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

    @staticmethod
    def _pick_device() -> str:
        setting = USE_GPU
        if setting == "true":
            return "cuda"
        if setting == "false":
            return "cpu"
        try:
            import torch
            if torch.cuda.is_available():
                log.info("CUDA GPU detected ✓")
                return "cuda"
        except ImportError:
            pass
        return "cpu"

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