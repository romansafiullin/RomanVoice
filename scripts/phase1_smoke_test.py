"""Phase 1 CUDA/Faster-Whisper smoke test for RomanVoice.

Run with:
    uv run python scripts/phase1_smoke_test.py

Pass --audio PATH to use your own roughly 10 second WAV. Without --audio, the
script tries to synthesize a short spoken WAV through Windows SAPI and falls
back to a silent WAV if speech synthesis is unavailable.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
import wave
from pathlib import Path

import ctranslate2
from faster_whisper import WhisperModel


ROOT = Path(__file__).resolve().parents[1]
SMOKE_DIR = ROOT / ".smoke"
DEFAULT_AUDIO = SMOKE_DIR / "phase1_10s.wav"


def run_command(args: list[str]) -> str:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return f"unavailable ({exc})"
    output = (completed.stdout or completed.stderr or "").strip()
    return output or f"exit={completed.returncode}"


def nvidia_info() -> dict[str, str]:
    query = "name,compute_cap,driver_version,memory.total"
    raw = run_command(
        [
            "nvidia-smi",
            f"--query-gpu={query}",
            "--format=csv,noheader",
        ]
    )
    if "unavailable" in raw or "exit=" in raw:
        return {"nvidia_smi": raw}
    first = raw.splitlines()[0]
    parts = [part.strip() for part in first.split(",")]
    keys = ["gpu_name", "compute_capability", "driver_version", "memory_total"]
    return dict(zip(keys, parts))


def synthesize_windows_sapi(path: Path) -> bool:
    phrase = (
        "Roman Voice phase one smoke test. "
        "This short recording checks local dictation on the Windows machine."
    )
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{path}'); "
        f"$s.Speak('{phrase}'); "
        "$s.Dispose();"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.returncode == 0 and path.exists() and path.stat().st_size > 1000


def create_silent_wav(path: Path, seconds: int = 10, sample_rate: int = 16000) -> None:
    frames = b"\x00\x00" * sample_rate * seconds
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(frames)


def ensure_audio(path_arg: str | None) -> Path:
    if path_arg:
        path = Path(path_arg).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        return path

    SMOKE_DIR.mkdir(parents=True, exist_ok=True)
    if not synthesize_windows_sapi(DEFAULT_AUDIO):
        create_silent_wav(DEFAULT_AUDIO)
    return DEFAULT_AUDIO


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", help="Path to a WAV/MP3/M4A test audio file.")
    parser.add_argument("--model", default="turbo")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="float16")
    args = parser.parse_args()

    audio_path = ensure_audio(args.audio)

    env = {
        "python": sys.version.replace("\n", " "),
        "platform": platform.platform(),
        "ctranslate2": ctranslate2.__version__,
        "faster_whisper_model": args.model,
        "device": args.device,
        "compute_type": args.compute_type,
        "audio_path": str(audio_path),
        **nvidia_info(),
    }

    start = time.perf_counter()
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    env["model_load_seconds"] = round(time.perf_counter() - start, 3)

    start = time.perf_counter()
    segments, info = model.transcribe(
        str(audio_path),
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
    )
    segment_list = list(segments)
    env["transcription_seconds"] = round(time.perf_counter() - start, 3)
    env["language"] = getattr(info, "language", None)
    env["language_probability"] = round(getattr(info, "language_probability", 0.0), 3)
    env["text"] = " ".join(segment.text.strip() for segment in segment_list).strip()

    print(json.dumps(env, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
