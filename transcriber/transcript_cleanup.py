"""Deterministic cleanup for Whisper dictation artifacts."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CleanupRule:
    name: str
    apply: Callable[[str], str]


@dataclass(frozen=True)
class GlossaryEntry:
    source: str
    target: str
    case_sensitive: bool = False
    whole_word: bool = True


class DeterministicTranscriptCleaner:
    """Apply narrow text rules without asking a model to rewrite the transcript."""

    def __init__(
        self,
        *,
        glossary_path: str | None = None,
        glossary_enabled: bool = True,
    ) -> None:
        self.glossary_path = glossary_path
        self.glossary_enabled = glossary_enabled
        self.rules = (
            CleanupRule("normalize_dot_time_minutes", self._normalize_dot_time_minutes),
            CleanupRule("normalize_time_meridiems", self._normalize_time_meridiems),
            CleanupRule("normalize_punctuation_spacing", self._normalize_punctuation_spacing),
            CleanupRule("normalize_inline_fragment_breaks", self._normalize_inline_fragment_breaks),
            CleanupRule("normalize_thousands_separators", self._normalize_thousands_separators),
            CleanupRule("normalize_time_colons", self._normalize_time_colons),
            CleanupRule("normalize_standalone_i", self._normalize_standalone_i),
            CleanupRule("apply_glossary", self._apply_glossary),
            CleanupRule("capitalize_sentence_starts", self._capitalize_sentence_starts),
            CleanupRule("ensure_terminal_punctuation", self._ensure_terminal_punctuation),
            CleanupRule("trim_repeated_tail_sentences", self._trim_repeated_tail_sentences),
        )

    def clean(self, transcript: str, *, enabled: bool = True) -> str:
        text = self._normalize_whitespace(transcript)
        if not text or not enabled:
            return text

        for rule in self.rules:
            text = rule.apply(text)

        return text

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()

    @staticmethod
    def _normalize_dot_time_minutes(text: str) -> str:
        time_cues = (
            r"after|around|at|before|between|by|from|near|till|until|about"
        )

        def replace(match: re.Match) -> str:
            return f"{match.group('cue')}{match.group('hour')}:{match.group('minutes')}"

        return re.sub(
            rf"\b(?P<cue>(?:{time_cues})\s+)"
            r"(?P<hour>0?[1-9]|1[0-2])\s*\.\s*(?P<minutes>[0-5]\d)\b",
            replace,
            text,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _normalize_time_meridiems(text: str) -> str:
        def replace(match: re.Match) -> str:
            hour = match.group("hour")
            minutes = match.group("minutes") or ""
            suffix = match.group("suffix").upper()
            return f"{hour}{minutes} {suffix}M"

        return re.sub(
            r"\b(?P<hour>\d{1,2})(?P<minutes>:\d{2})?\s+"
            r"(?P<suffix>[ap])\s*\.?\s*m\s*\.?\b",
            replace,
            text,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _normalize_punctuation_spacing(text: str) -> str:
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        return re.sub(r"([,.;:!?])(?=\S)", r"\1 ", text)

    @staticmethod
    def _normalize_inline_fragment_breaks(text: str) -> str:
        continuation = (
            r"mine(?:'s)?|yours?|ours?|his|hers?|theirs?|my|our|your|her|their"
        )

        text = re.sub(r"\b(?P<meridiem>[AP]M)\.\s*,\s*", r"\g<meridiem>, ", text)

        def replace_meridiem_fragment(match: re.Match) -> str:
            return f"{match.group('meridiem')}, {match.group('next').lower()}"

        text = re.sub(
            rf"\b(?P<meridiem>[AP]M)\.\s+(?P<next>{continuation})\b",
            replace_meridiem_fragment,
            text,
            flags=re.IGNORECASE,
        )

        def replace_ordinal_fragment(match: re.Match) -> str:
            return f"{match.group('ordinal')}, {match.group('next').lower()}"

        return re.sub(
            rf"\b(?P<ordinal>the\s+\d{{1,2}}(?:st|nd|rd|th))\.\s+"
            rf"(?P<next>{continuation})\b",
            replace_ordinal_fragment,
            text,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _normalize_thousands_separators(text: str) -> str:
        return re.sub(r"(?<=\d),\s+(?=\d{3}\b)", ",", text)

    @staticmethod
    def _normalize_time_colons(text: str) -> str:
        return re.sub(r"\b(\d{1,2}):\s+(\d{2})\b", r"\1:\2", text)

    @staticmethod
    def _normalize_standalone_i(text: str) -> str:
        return re.sub(r"\bi\b", "I", text)

    def _apply_glossary(self, text: str) -> str:
        if not self.glossary_enabled:
            return text

        for entry in self._load_glossary_entries():
            text = self._replace_glossary_entry(text, entry)

        return text

    def _load_glossary_entries(self) -> list[GlossaryEntry]:
        if not self.glossary_path or not os.path.exists(self.glossary_path):
            return []

        try:
            with open(self.glossary_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load transcript glossary %s: %s", self.glossary_path, exc)
            return []

        entries = self._parse_glossary_entries(data)
        if not entries:
            logger.warning("Transcript glossary %s did not contain usable entries", self.glossary_path)
        return entries

    @classmethod
    def _parse_glossary_entries(cls, data: Any) -> list[GlossaryEntry]:
        if isinstance(data, dict) and isinstance(data.get("replacements"), dict):
            return cls._entries_from_mapping(data["replacements"])

        if isinstance(data, dict):
            return cls._entries_from_mapping(data)

        if isinstance(data, list):
            entries = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                source = str(item.get("from") or item.get("source") or "").strip()
                target = str(item.get("to") or item.get("target") or "").strip()
                if not source or not target:
                    continue
                entries.append(
                    GlossaryEntry(
                        source=source,
                        target=target,
                        case_sensitive=bool(item.get("case_sensitive", False)),
                        whole_word=bool(item.get("whole_word", True)),
                    )
                )
            return entries

        return []

    @staticmethod
    def _entries_from_mapping(mapping: dict[str, Any]) -> list[GlossaryEntry]:
        entries = []
        for source, target in mapping.items():
            source_text = str(source).strip()
            target_text = str(target).strip()
            if source_text and target_text:
                entries.append(GlossaryEntry(source=source_text, target=target_text))
        return entries

    @staticmethod
    def _replace_glossary_entry(text: str, entry: GlossaryEntry) -> str:
        pattern = re.escape(entry.source)
        if entry.whole_word:
            if entry.source[0].isalnum() or entry.source[0] == "_":
                pattern = r"(?<!\w)" + pattern
            if entry.source[-1].isalnum() or entry.source[-1] == "_":
                pattern += r"(?!\w)"

        flags = 0 if entry.case_sensitive else re.IGNORECASE
        return re.sub(pattern, entry.target, text, flags=flags)

    @staticmethod
    def _capitalize_sentence_starts(text: str) -> str:
        chars = list(text)
        capitalize_next = True
        for index, char in enumerate(chars):
            if char.isalpha():
                if capitalize_next:
                    chars[index] = char.upper()
                capitalize_next = False
            elif char in ".!?":
                capitalize_next = True
            elif not char.isspace() and char not in "\"'“”‘’([{":
                capitalize_next = False
        return "".join(chars)

    @staticmethod
    def _ensure_terminal_punctuation(text: str) -> str:
        if text and text[-1] not in ".!?…":
            return text + "."
        return text

    @classmethod
    def _trim_repeated_tail_sentences(cls, text: str) -> str:
        sentences = re.findall(r"\s*[^.!?…]+[.!?…]+", text or "")
        if len(sentences) < 4:
            return text

        last = cls._normalize_repeated_sentence(sentences[-1])
        if not last:
            return text

        repeated_count = 1
        for sentence in reversed(sentences[:-1]):
            if cls._normalize_repeated_sentence(sentence) != last:
                break
            repeated_count += 1

        if repeated_count < 4:
            return text

        keep_count = len(sentences) - repeated_count + 1
        trimmed = "".join(sentences[:keep_count]).strip()
        logger.info(
            "Trimmed repeated transcript tail sentence (%s repeated copies removed)",
            repeated_count - 1,
        )
        return trimmed or text

    @staticmethod
    def _normalize_repeated_sentence(sentence: str) -> str:
        return re.sub(r"\W+", " ", sentence or "").strip().lower()


def clean_transcript_text(
    transcript: str,
    *,
    enabled: bool = True,
    glossary_path: str | None = None,
    glossary_enabled: bool = True,
) -> str:
    cleaner = DeterministicTranscriptCleaner(
        glossary_path=glossary_path,
        glossary_enabled=glossary_enabled,
    )
    return cleaner.clean(transcript, enabled=enabled)
