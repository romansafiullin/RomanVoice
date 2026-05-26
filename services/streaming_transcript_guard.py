"""Choose between final and streaming transcripts for long dictations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from config import config

_PREFIX_MISSING_MIN_CHARS = 80
_FINAL_PREFIX_OFFSET_MAX_CHARS = 40
_OVERLAP_MIN_CHARS = 180
_OVERLAP_FINAL_RATIO = 0.55


@dataclass(frozen=True)
class StreamingTranscriptDecision:
    prefer_streaming: bool
    reason: str = ""
    char_delta: int = 0
    final_ratio: float = 0.0
    missing_prefix_chars: int = 0
    overlap_chars: int = 0
    overlap_final_ratio: float = 0.0


def choose_streaming_transcript(
    final_text: str,
    streaming_text: str,
    *,
    duration_seconds: float,
) -> StreamingTranscriptDecision:
    """Return whether the live transcript is safer than the final batch pass.

    The final Whisper pass is usually better, but for long dictations it can
    occasionally drop the beginning while still returning a similar total length.
    The live rolling transcript is useful as a guard against that failure mode.
    """
    final_text = (final_text or "").strip()
    streaming_text = (streaming_text or "").strip()
    if not final_text or not streaming_text:
        return StreamingTranscriptDecision(False)

    char_delta = len(streaming_text) - len(final_text)
    final_ratio = len(final_text) / max(len(streaming_text), 1)
    if (
        duration_seconds < config.LONG_FORM_STREAMING_FALLBACK_MIN_SECONDS
        or len(streaming_text) < config.LONG_FORM_STREAMING_FALLBACK_MIN_CHARS
    ):
        return StreamingTranscriptDecision(
            False,
            char_delta=char_delta,
            final_ratio=final_ratio,
        )

    if (
        char_delta >= config.LONG_FORM_STREAMING_FALLBACK_MIN_CHAR_DELTA
        and final_ratio <= config.LONG_FORM_STREAMING_FALLBACK_RATIO
    ):
        return StreamingTranscriptDecision(
            True,
            reason="final_much_shorter",
            char_delta=char_delta,
            final_ratio=final_ratio,
        )

    prefix_decision = _prefix_truncation_decision(
        final_text,
        streaming_text,
        char_delta=char_delta,
        final_ratio=final_ratio,
    )
    if prefix_decision.prefer_streaming:
        return prefix_decision

    return StreamingTranscriptDecision(
        False,
        char_delta=char_delta,
        final_ratio=final_ratio,
        missing_prefix_chars=prefix_decision.missing_prefix_chars,
        overlap_chars=prefix_decision.overlap_chars,
        overlap_final_ratio=prefix_decision.overlap_final_ratio,
    )


def _prefix_truncation_decision(
    final_text: str,
    streaming_text: str,
    *,
    char_delta: int,
    final_ratio: float,
) -> StreamingTranscriptDecision:
    final_normalized = _normalize_for_overlap(final_text)
    streaming_normalized = _normalize_for_overlap(streaming_text)
    if not final_normalized or not streaming_normalized:
        return StreamingTranscriptDecision(False, char_delta=char_delta, final_ratio=final_ratio)

    match = max(
        SequenceMatcher(
            None,
            streaming_normalized,
            final_normalized,
            autojunk=False,
        ).get_matching_blocks(),
        key=lambda item: item.size,
    )
    overlap_final_ratio = match.size / max(len(final_normalized), 1)
    prefer = (
        match.a >= _PREFIX_MISSING_MIN_CHARS
        and match.b <= _FINAL_PREFIX_OFFSET_MAX_CHARS
        and match.size >= _OVERLAP_MIN_CHARS
        and overlap_final_ratio >= _OVERLAP_FINAL_RATIO
    )
    return StreamingTranscriptDecision(
        prefer,
        reason="final_missing_prefix" if prefer else "",
        char_delta=char_delta,
        final_ratio=final_ratio,
        missing_prefix_chars=match.a,
        overlap_chars=match.size,
        overlap_final_ratio=overlap_final_ratio,
    )


def _normalize_for_overlap(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()
