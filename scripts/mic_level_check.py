"""Rank local input devices by captured level.

Run this while speaking normally:
    uv run --python 3.12 python scripts/mic_level_check.py --seconds 3
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import sounddevice as sd


def measure_device(device_id: int, seconds: float) -> dict:
    device = sd.query_devices()[device_id]
    samplerate = int(device.get("default_samplerate") or 44100)
    channels = min(1, int(device.get("max_input_channels") or 1))
    audio = sd.rec(
        int(samplerate * seconds),
        samplerate=samplerate,
        channels=channels,
        dtype="int16",
        device=device_id,
    )
    sd.wait()
    arr = np.asarray(audio, dtype=np.int16)
    peak = int(np.max(np.abs(arr))) if arr.size else 0
    rms = float(np.sqrt(np.mean(arr.astype(np.float64) ** 2))) if arr.size else 0.0
    return {
        "id": device_id,
        "name": device["name"],
        "rate": samplerate,
        "rms": round(rms, 2),
        "peak": peak,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--contains", default="")
    args = parser.parse_args()

    hostapis = sd.query_hostapis()
    results = []
    for index, device in enumerate(sd.query_devices()):
        if device.get("max_input_channels", 0) <= 0:
            continue
        if args.contains and args.contains.lower() not in device["name"].lower():
            continue
        try:
            row = measure_device(index, args.seconds)
            row["hostapi"] = hostapis[device["hostapi"]]["name"]
            row["default"] = index == sd.default.device[0]
        except Exception as exc:
            row = {
                "id": index,
                "name": device["name"],
                "hostapi": hostapis[device["hostapi"]]["name"],
                "error": str(exc),
                "default": index == sd.default.device[0],
            }
        results.append(row)

    for row in sorted(results, key=lambda item: item.get("peak", -1), reverse=True):
        print(json.dumps(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
