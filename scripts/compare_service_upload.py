from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from config import config
from services.dictation_service import http_batch_decode_options
from transcriber.local_backend import LocalWhisperBackend


def profile_options() -> dict[str, dict[str, Any]]:
    current = LocalWhisperBackend.decode_options()
    legacy = LocalWhisperBackend.decode_options(
        {
            "language": "en",
            "condition_on_previous_text": True,
            "initial_prompt": config.FASTER_WHISPER_LEGACY_DICTATION_PROMPT,
            "vad_filter": True,
            "vad_parameters": {
                "min_silence_duration_ms": config.FASTER_WHISPER_VAD_MIN_SILENCE_MS,
            },
        }
    )
    http_batch = LocalWhisperBackend.decode_options(http_batch_decode_options())
    vad_off = dict(current)
    vad_off["vad_filter"] = False
    vad_off.pop("vad_parameters", None)
    return {
        "current": current,
        "legacy_context": legacy,
        config.SERVICE_HTTP_DECODE_PROFILE: http_batch,
        "current_vad_off": vad_off,
    }


def run_profile(
    backend: LocalWhisperBackend,
    audio_path: Path,
    name: str,
    options: dict[str, Any],
) -> dict[str, Any]:
    text = backend.transcribe(str(audio_path), decode_options=options)
    return {
        "profile": name,
        "chars": len(text),
        "text": text,
        "decode": {
            "language": options.get("language"),
            "condition_on_previous_text": options.get("condition_on_previous_text"),
            "vad_filter": options.get("vad_filter"),
            "vad_parameters": options.get("vad_parameters"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare RomanVoice decode profiles against a retained service upload."
    )
    parser.add_argument("audio_path", type=Path)
    parser.add_argument("--model", default="auto")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    audio_path = args.audio_path.expanduser()
    if not audio_path.exists():
        raise SystemExit(f"Audio file not found: {audio_path}")

    backend = LocalWhisperBackend(model_name=args.model)
    results = [
        run_profile(backend, audio_path, name, options)
        for name, options in profile_options().items()
    ]

    if args.as_json:
        print(json.dumps({"audio_path": str(audio_path), "results": results}, indent=2))
        return 0

    print(f"Audio: {audio_path}")
    for result in results:
        print()
        print(f"--- {result['profile']} ({result['chars']} chars) ---")
        decode = result["decode"]
        print(
            "language={language} condition_on_previous_text={condition} "
            "vad_filter={vad}".format(
                language=decode["language"],
                condition=decode["condition_on_previous_text"],
                vad=decode["vad_filter"],
            )
        )
        print(result["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
