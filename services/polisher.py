"""Optional local transcript polishing through Ollama."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from config import config

logger = logging.getLogger(__name__)


@dataclass
class PolishResult:
    text: str
    used_polish: bool
    error: str | None = None


class LocalPolisher:
    """Clean dictation text with a small local Ollama model when enabled."""

    def maybe_polish(
        self,
        transcript: str,
        *,
        enabled: bool,
        model: str,
        word_threshold: int,
        timeout_ms: int,
        ollama_url: str,
    ) -> PolishResult:
        raw_text = transcript.strip()
        if not raw_text:
            return PolishResult(raw_text, used_polish=False)

        if not enabled:
            return PolishResult(raw_text, used_polish=False)

        if self._word_count(raw_text) < word_threshold:
            return PolishResult(raw_text, used_polish=False)

        try:
            polished = self._call_ollama(
                raw_text,
                model=model or config.POLISH_MODEL,
                timeout_ms=timeout_ms,
                ollama_url=ollama_url or config.POLISH_OLLAMA_URL,
            )
        except Exception as exc:
            logger.warning("Local polish failed; using raw transcript: %s", exc)
            return PolishResult(raw_text, used_polish=False, error=str(exc))

        if not self._usable(raw_text, polished):
            logger.warning("Local polish returned unusable output; using raw transcript")
            return PolishResult(raw_text, used_polish=False, error="unusable polish output")

        return PolishResult(polished, used_polish=True)

    def _call_ollama(
        self,
        transcript: str,
        *,
        model: str,
        timeout_ms: int,
        ollama_url: str,
    ) -> str:
        endpoint = ollama_url.rstrip("/") + "/api/generate"
        timeout_seconds = max(0.1, timeout_ms / 1000.0)
        prompt = (
            "Clean up this voice dictation transcript. Preserve the speaker's "
            "meaning and wording, add punctuation/capitalization, remove obvious "
            "filler and false starts, and do not add facts. Return only the final "
            "text.\n\nTranscript:\n"
            f"{transcript}"
        )
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "top_p": 0.9,
            },
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama request failed: {exc}") from exc

        data = json.loads(body)
        return str(data.get("response", "")).strip()

    @staticmethod
    def _word_count(text: str) -> int:
        return len(re.findall(r"\S+", text))

    @staticmethod
    def _usable(raw_text: str, polished_text: str) -> bool:
        if not polished_text:
            return False
        if len(polished_text) > max(500, len(raw_text) * 3):
            return False
        lower = polished_text.lower()
        refusal_markers = (
            "i can't",
            "i cannot",
            "as an ai",
            "i'm unable",
        )
        return not any(marker in lower for marker in refusal_markers)


local_polisher = LocalPolisher()
