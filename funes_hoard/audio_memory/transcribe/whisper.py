"""faster-whisper transcriber: model files under <DATA_DIR>/models, lazy load, cuda when available."""

from __future__ import annotations

import logging
import os
import sys
import sysconfig
import threading
import time
from pathlib import Path

import numpy as np

from ..audio.wav import to_float32
from funes_hoard.hoard_link.lease import LeaseError, LeaseTimeout, lease
from .base import Segment, Transcriber, Word
from .filter import COMPRESSION_MAX, LOGPROB_MIN, NO_SPEECH_MAX

log = logging.getLogger("scribe.whisper")
REPO = "Systran/faster-whisper-{size}"
DISTIL_REPO = "Systran/distil-whisper-{size}"

# ---------- GPU memory leasing (Hoard Link) ----------
# Before loading the model on CUDA, ask the family's GPU-lease hub for room so
# Scribe does not collide with the LLM server / ComfyUI on the same machine.
# One place for the per-size VRAM estimates; override with SCRIBE_WHISPER_VRAM_MB.
VRAM_MB_BY_SIZE = {
    "tiny": 1024,
    "base": 1536,
    "small": 2048,
    "medium": 5120,
    "large": 6144,
    "large-v1": 6144,
    "large-v2": 6144,
    "large-v3": 6144,
    "turbo": 6144,
    "large-v3-turbo": 6144,
}
DEFAULT_VRAM_MB = 2048  # unknown/custom size (e.g. a distil- variant): assume "small"-sized
DEFAULT_LEASE_TIMEOUT_S = 120.0
GPU_LEASE_STATES = ("waiting", "granted", "fallback_cpu", "disabled")


def gpu_leasing_enabled() -> bool:
    return os.environ.get("SCRIBE_GPU_LEASE", "1").strip() != "0"


def whisper_vram_mb(size: str) -> int:
    override = os.environ.get("SCRIBE_WHISPER_VRAM_MB", "").strip()
    if override:
        try:
            return int(override)
        except ValueError:
            log.warning("SCRIBE_WHISPER_VRAM_MB=%r is not an int; ignoring", override)
    return VRAM_MB_BY_SIZE.get(size.replace("distil-", ""), DEFAULT_VRAM_MB)


def lease_timeout_s() -> float:
    raw = os.environ.get("SCRIBE_LEASE_TIMEOUT_S", "").strip()
    if not raw:
        return DEFAULT_LEASE_TIMEOUT_S
    try:
        return float(raw)
    except ValueError:
        log.warning("SCRIBE_LEASE_TIMEOUT_S=%r is not a number; using default", raw)
        return DEFAULT_LEASE_TIMEOUT_S


_DLL_DIRS_ADDED = False


def add_cuda_dll_dirs() -> list[str]:
    """Windows: CTranslate2 loads cuBLAS/cuDNN lazily at the first inference and
    finds them only through the DLL search path. The pip wheels
    (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, in requirements-windows.txt) put
    them under site-packages/nvidia/<lib>/bin, which is NOT on that path, so the
    model loads fine and then fails with "cublas64_12.dll is not found" — seen on
    the first real recording. Registering those folders once fixes it."""
    global _DLL_DIRS_ADDED
    if _DLL_DIRS_ADDED or sys.platform != "win32":
        return []
    _DLL_DIRS_ADDED = True
    found: list[str] = []
    for site in sysconfig.get_paths().values():
        base = Path(site) / "nvidia"
        if not base.is_dir():
            continue
        for lib in sorted(base.iterdir()):
            bin_dir = lib / "bin"
            if bin_dir.is_dir() and str(bin_dir) not in found:
                found.append(str(bin_dir))
    for folder in found:
        try:
            os.add_dll_directory(folder)
        except (AttributeError, OSError):
            pass
    if found:
        os.environ["PATH"] = os.pathsep.join(found + [os.environ.get("PATH", "")])
        log.info("cuda dll dirs: %s", found)
    return found


def _looks_like_cuda_error(error: BaseException) -> bool:
    text = str(error).lower()
    return any(k in text for k in ("cublas", "cudnn", "cuda", "cudart", "device-side"))


def cuda_available() -> bool:
    try:
        add_cuda_dll_dirs()
        import ctranslate2  # type: ignore

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def resolve_device(device: str) -> str:
    if device == "auto":
        return "cuda" if cuda_available() else "cpu"
    return device


def resolve_compute(compute: str, device: str) -> str:
    if compute != "auto":
        return compute
    return "float16" if device == "cuda" else "int8"


def model_repo(size: str) -> str:
    return (DISTIL_REPO if size.startswith("distil-") else REPO).format(size=size.replace("distil-", ""))


def model_present(models_dir: Path, size: str) -> bool:
    """True when the hub cache under models_dir already holds model.bin for this size."""
    folder = models_dir / ("models--" + model_repo(size).replace("/", "--"))
    return any(folder.glob("snapshots/*/model.bin")) if folder.is_dir() else False


class WhisperTranscriber(Transcriber):
    name = "faster-whisper"

    def __init__(self, models_dir: Path, size: str = "small", device: str = "auto", compute_type: str = "auto"):
        self.models_dir = models_dir
        self.size, self.device_setting, self.compute_setting = size, device, compute_type
        self.model = None
        self.loaded_key: tuple | None = None
        self.state = "idle"  # idle | downloading | loading | ready | error
        self.error = ""
        self.last_ms: int | None = None
        self.cpu_fallback_reason = ""
        self.lock = threading.RLock()
        # GPU lease (see module docstring block above): held from the CUDA load
        # through the first transcription, then released; state is surfaced in info().
        self.gpu_lease_state = "disabled"
        self._active_lease = None
        self._lease_pending_release = False

    # ---------- configuration ----------
    def reconfigure(self, size: str | None = None, device: str | None = None, compute_type: str | None = None, **_: object) -> None:
        with self.lock:
            self.size = size or self.size
            self.device_setting = device or self.device_setting
            self.compute_setting = compute_type or self.compute_setting
            if self.loaded_key != self._key():
                self._release_active_lease()
                self.model, self.loaded_key, self.state = None, None, "idle"
                self.gpu_lease_state = "disabled"

    def _key(self) -> tuple:
        device = resolve_device(self.device_setting)
        return (self.size, device, resolve_compute(self.compute_setting, device))

    # ---------- GPU lease helpers ----------
    def _release_active_lease(self) -> None:
        """Drop any GPU lease this transcriber holds. Leaves gpu_lease_state alone
        so info() keeps showing the outcome of the last attempt (granted/fallback_cpu)."""
        lease_obj, self._active_lease = self._active_lease, None
        self._lease_pending_release = False
        if lease_obj is not None:
            try:
                lease_obj.release()
            except Exception as error:  # pragma: no cover - best effort cleanup
                log.warning("failed to release GPU lease: %s", error)

    def ensure_loaded(self) -> None:
        with self.lock:
            key = self._key()
            if self.model is not None and self.loaded_key == key:
                return
            self._release_active_lease()
            add_cuda_dll_dirs()
            from faster_whisper import WhisperModel  # heavy import, kept lazy

            size, device, compute = key
            self.state = "loading" if model_present(self.models_dir, size) else "downloading"
            self.error = ""
            self.models_dir.mkdir(parents=True, exist_ok=True)

            gpu_lease_obj = None
            if device != "cuda":
                self.gpu_lease_state = "disabled"
            elif not gpu_leasing_enabled():
                self.gpu_lease_state = "disabled"
            else:
                self.gpu_lease_state = "waiting"
                try:
                    gpu_lease_obj = lease(
                        vram_mb=whisper_vram_mb(size),
                        purpose=f"whisper {size}",
                        owner="scribe",
                        timeout_s=lease_timeout_s(),
                    ).acquire()
                    self.gpu_lease_state = "granted"
                except (LeaseTimeout, LeaseError) as error:
                    log.warning("GPU lease unavailable (%s); falling back to cpu for this load", error)
                    self.gpu_lease_state = "fallback_cpu"
                    device = "cpu"
                    compute = resolve_compute(self.compute_setting, "cpu")
                    key = (size, device, compute)

            try:
                started = time.time()
                self.model = WhisperModel(size, device=device, compute_type=compute, download_root=str(self.models_dir))
                self.loaded_key, self.state = key, "ready"
                log.info("whisper %s loaded on %s/%s in %.1fs", size, device, compute, time.time() - started)
                if gpu_lease_obj is not None:
                    self._active_lease, self._lease_pending_release = gpu_lease_obj, True
            except Exception as error:
                if gpu_lease_obj is not None:
                    self._active_lease = gpu_lease_obj
                    self._release_active_lease()
                if device == "cuda":  # fall back to CPU rather than fail the whole session
                    log.warning("cuda load failed (%s); falling back to cpu", error)
                    try:
                        compute = resolve_compute(self.compute_setting, "cpu")
                        self.model = WhisperModel(size, device="cpu", compute_type=compute, download_root=str(self.models_dir))
                        self.loaded_key, self.state = (size, "cpu", compute), "ready"
                        return
                    except Exception as inner:
                        error = inner
                self.model, self.loaded_key, self.state, self.error = None, None, "error", str(error)
                raise

    # ---------- work ----------
    def transcribe(self, samples: np.ndarray, language: str | None = None) -> list[Segment]:
        self.ensure_loaded()
        audio = to_float32(samples)
        if len(audio) == 0:
            return []
        started = time.time()
        with self.lock:
            try:
                try:
                    raw, _info = self._run(audio, language)
                except Exception as error:
                    # A CUDA library missing at inference time (not at load time) is
                    # the failure seen on Windows without the cuBLAS/cuDNN wheels.
                    # Do not leave the session stuck: reload on CPU and carry on.
                    if self.loaded_key and self.loaded_key[1] == "cuda" and _looks_like_cuda_error(error):
                        log.warning("cuda inference failed (%s); reloading on cpu", error)
                        self.cpu_fallback_reason = str(error)
                        self.device_setting = "cpu"
                        self._release_active_lease()
                        self.model, self.loaded_key = None, None
                        self.ensure_loaded()
                        raw, _info = self._run(audio, language)
                    else:
                        raise
            finally:
                # The lease only needs to cover the load plus this first inference
                # (nvidia-smi already shows the model's memory after that); release
                # it here whether or not this call ended up succeeding.
                if self._lease_pending_release:
                    self._release_active_lease()
            segments = [
                Segment(
                    round(float(s.start), 2),
                    round(float(s.end), 2),
                    s.text.strip(),
                    round(float(np.exp(s.avg_logprob)) if s.avg_logprob is not None else 0.0, 3),
                    [Word(round(float(w.start), 2), round(float(w.end), 2), w.word, round(float(w.probability), 3)) for w in (s.words or [])],
                    no_speech_prob=round(float(s.no_speech_prob), 3) if s.no_speech_prob is not None else None,
                    avg_logprob=round(float(s.avg_logprob), 3) if s.avg_logprob is not None else None,
                    compression_ratio=round(float(s.compression_ratio), 3) if s.compression_ratio is not None else None,
                )
                for s in raw
                if s.text.strip()
            ]
        self.last_ms = int((time.time() - started) * 1000)
        return segments

    def _run(self, audio: np.ndarray, language: str | None):
        return self.model.transcribe(
            audio,
            language=None if language in (None, "", "auto") else language,
            beam_size=2,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            word_timestamps=True,
            condition_on_previous_text=False,
            # explicit decoder guards; filter.py applies the same limits again per segment
            no_speech_threshold=NO_SPEECH_MAX,
            log_prob_threshold=LOGPROB_MIN,
            compression_ratio_threshold=COMPRESSION_MAX,
        )

    def info(self) -> dict:
        size, device, compute = self._key()
        return {
            "name": self.name,
            "model": size,
            "device": device,
            "compute_type": compute,
            "cuda": cuda_available(),
            "loaded": self.model is not None and self.loaded_key == (size, device, compute),
            "download": "ready" if model_present(self.models_dir, size) else ("downloading" if self.state == "downloading" else "missing"),
            "state": self.state,
            "error": self.error,
            "cpu_fallback_reason": self.cpu_fallback_reason,
            "last_ms": self.last_ms,
            "models_dir": str(self.models_dir),
            "gpu_lease": self.gpu_lease_state,
        }
