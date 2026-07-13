"""
Configuration constants for the OpenWhisper application.
"""
import os
import secrets
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Dict, List, Tuple

try:
    import numpy as np
except ImportError:  # pragma: no cover - lightweight fallback for test/import environments
    np = SimpleNamespace(int16="int16")


def _appdata_dir() -> str:
    base = os.environ.get("APPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(base, "RomanVoice")


def _local_appdata_dir() -> str:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "RomanVoice")


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value.strip())
    except ValueError:
        return default


def _start_hidden_default() -> bool:
    # The full window should only appear from an explicit UI/debug launcher.
    return not _env_bool("ROMANVOICE_FORCE_SHOW", False)


def _service_token_file() -> str:
    return os.environ.get(
        "ROMANVOICE_SERVICE_TOKEN_FILE",
        os.path.join(_appdata_dir(), "service_token.txt"),
    )


def _service_token_default() -> str:
    token = os.environ.get("ROMANVOICE_SERVICE_TOKEN", "").strip()
    if token:
        return token

    token_file = _service_token_file()
    try:
        if os.path.exists(token_file):
            with open(token_file, "r", encoding="utf-8") as handle:
                token = handle.read().strip()
            if token:
                return token
    except OSError:
        pass

    return ""


def ensure_service_token() -> str:
    if config.SERVICE_TOKEN:
        return config.SERVICE_TOKEN

    token = secrets.token_urlsafe(32)
    token_file = config.SERVICE_TOKEN_FILE
    parent = os.path.dirname(token_file)
    temp_file = f"{token_file}.{secrets.token_hex(6)}.tmp"
    try:
        os.makedirs(parent, exist_ok=True)
        descriptor = os.open(temp_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_file, token_file)
        os.chmod(token_file, 0o600)
    except OSError as exc:
        try:
            os.remove(temp_file)
        except OSError:
            pass
        raise RuntimeError(
            f"Unable to persist the RomanVoice service token at {token_file}"
        ) from exc
    config.SERVICE_TOKEN = token
    return token


def service_token_configuration() -> dict[str, bool | str]:
    """Return non-secret token custody information for diagnostics."""
    environment_token = os.environ.get("ROMANVOICE_SERVICE_TOKEN", "").strip()
    file_token = ""
    try:
        with open(config.SERVICE_TOKEN_FILE, "r", encoding="utf-8") as handle:
            file_token = handle.read().strip()
    except OSError:
        pass

    active_token = config.SERVICE_TOKEN
    if environment_token:
        source = "environment"
    elif file_token:
        source = "file"
    elif active_token:
        source = "memory"
    else:
        source = "missing"

    return {
        "source": source,
        "file_present": bool(file_token),
        "environment_present": bool(environment_token),
        "environment_file_mismatch": bool(
            environment_token and file_token and environment_token != file_token
        ),
        "active_file_mismatch": bool(
            active_token and file_token and active_token != file_token
        ),
    }


@dataclass
class AppConfig:
    """Centralized configuration for the OpenWhisper application."""

    # File paths
    APP_NAME: str = "RomanVoice"
    APPDATA_DIR: str = field(default_factory=_appdata_dir)
    LOCAL_APPDATA_DIR: str = field(default_factory=_local_appdata_dir)
    SETTINGS_FILE: str = field(
        default_factory=lambda: os.path.join(_appdata_dir(), "config.json")
    )
    RECORDED_AUDIO_FILE: str = field(
        default_factory=lambda: os.path.join(_local_appdata_dir(), "recorded_audio.wav")
    )
    LOG_FILE: str = field(
        default_factory=lambda: os.path.join(_local_appdata_dir(), "romanvoice.log")
    )
    ENV_FILE: str = ".env"

    # Logging configuration
    LOG_LEVEL: str = os.environ.get("OPENWHISPER_LOG_LEVEL", "INFO").upper()
    LOG_FORMAT: str = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    LOG_MAX_BYTES: int = 5 * 1024 * 1024
    LOG_BACKUP_COUNT: int = 3

    # History and recordings
    HISTORY_FILE: str = field(
        default_factory=lambda: os.path.join(_appdata_dir(), "transcription_history.json")
    )
    RECORDINGS_FOLDER: str = field(
        default_factory=lambda: os.path.join(_local_appdata_dir(), "recordings")
    )
    MAX_SAVED_RECORDINGS: int = 3
    DATABASE_FILE: str = field(
        default_factory=lambda: os.path.join(_appdata_dir(), "history.sqlite")
    )
    HISTORY_ENABLED: bool = True
    MAX_HISTORY_ENTRIES: int = 1000

    # Audio settings
    CHUNK_SIZE: int = 1024
    AUDIO_FORMAT: type = np.int16  # NumPy dtype for audio format
    CHANNELS: int = 1
    SAMPLE_RATE: int = 44100
    MIN_MIC_INPUT_PEAK: int = 100
    PREFER_WASAPI_INPUT: bool = True

    # Default hotkeys
    DEFAULT_HOTKEYS: Dict[str, str] = None

    # Model configurations
    MODEL_CHOICES: Tuple[str, ...] = (
        'Local Whisper',
    )

    MODEL_VALUE_MAP: Dict[str, str] = None

    # Whisper model choices for faster-whisper
    WHISPER_MODEL_CHOICES: List[str] = None

    # Main window sizing
    MAIN_WINDOW_MIN_WIDTH: int = 500
    MAIN_WINDOW_MIN_HEIGHT: int = 600
    MAIN_WINDOW_DEFAULT_WIDTH: int = 605
    MAIN_WINDOW_DEFAULT_HEIGHT: int = 840
    MAIN_WINDOW_HISTORY_SIDEBAR_WIDTH: int = 380
    MAIN_WINDOW_HISTORY_EDGE_TAB_WIDTH: int = 24
    MAIN_WINDOW_MAX_WIDTH: int = 1280
    MAIN_WINDOW_COLLAPSED_RESTORE_MAX_HEIGHT: int = 840

    # Waveform overlay settings
    WAVEFORM_OVERLAY_WIDTH: int = 300
    WAVEFORM_OVERLAY_HEIGHT: int = 80
    WAVEFORM_BAR_COUNT: int = 20
    WAVEFORM_BAR_WIDTH: int = 8
    WAVEFORM_BAR_SPACING: int = 2
    WAVEFORM_FRAME_RATE: int = 30
    WAVEFORM_LEVEL_SMOOTHING: float = 0.7

    # Waveform colors (hex format)
    WAVEFORM_BG_COLOR: str = "#1a1a1a"
    WAVEFORM_ACCENT_COLOR: str = "#00d4ff"
    WAVEFORM_SECONDARY_COLOR: str = "#0099cc"
    WAVEFORM_TEXT_COLOR: str = "#ffffff"

    # Streaming text overlay settings
    STREAMING_TEXT_OVERLAY_ENABLED: bool = False
    STREAMING_OVERLAY_WIDTH: int = 450
    STREAMING_OVERLAY_MIN_HEIGHT: int = 100
    STREAMING_OVERLAY_MAX_HEIGHT: int = 300
    STREAMING_OVERLAY_FONT_SIZE: int = 12

    # Timing settings
    HOTKEY_DEBOUNCE_MS: int = 300
    OVERLAY_HIDE_DELAY_MS: int = 1500
    CANCELLATION_ANIMATION_DURATION_MS: int = 800
    CANCELLATION_GRACE_MS: int = 200  # Extra delay after cancel animation before hiding overlay
    PROGRESS_BAR_INTERVAL_MS: int = 10
    # Continue capturing this many ms after stop to avoid end cut-offs
    POST_ROLL_MS: int = 1200
    # How long to wait for the recorder thread to flush post-roll frames before saving
    POST_ROLL_FINALIZE_GRACE_MS: int = 800
    # Extra silence appended to the end of saved audio so ASR models don't drop the last word
    END_PADDING_MS: int = 500
    AUTO_STOP_ON_SILENCE: bool = True
    AUTO_STOP_SILENCE_SECONDS: float = 15.0
    AUTO_STOP_SPEECH_LEVEL_THRESHOLD: float = 0.0025
    AUTO_STOP_CHECK_INTERVAL_MS: int = 500
    # Hotkey watchdog: detects sleep/resume gaps; periodic refresh re-registers the hook
    ENABLE_GLOBAL_HOTKEYS: bool = _env_bool("ROMANVOICE_ENABLE_GLOBAL_HOTKEYS", True)
    HOTKEY_BACKEND: str = "win32"
    HOTKEY_WATCHDOG_INTERVAL_MS: int = 10_000
    HOTKEY_SLEEP_GAP_THRESHOLD_SEC: float = 30.0
    HOTKEY_HOOK_REFRESH_INTERVAL_MS: int = 5 * 60 * 1000
    COMPACT_STATUS_OVERLAY: bool = True
    # Direct typing is the primary feedback surface; avoid a duplicate transcript popup.
    COMPACT_OVERLAY_LIVE_PREVIEW: bool = False
    COMPACT_OVERLAY_POSITION: str = "bottom_center"  # "bottom_center" or "bottom_right"
    START_HIDDEN_TO_TRAY: bool = field(default_factory=_start_hidden_default)
    # Whisper expects 16 kHz audio regardless of recorder sample rate
    WHISPER_TARGET_SAMPLE_RATE: int = 16000

    # Audio splitting settings
    MAX_FILE_SIZE_MB: int = 23  # Maximum file size before splitting
    SILENCE_THRESHOLD: float = 0.01  # Volume threshold to detect silence
    MIN_CHUNK_DURATION_SEC: int = 30  # Minimum duration for each chunk in seconds
    SILENCE_DURATION_SEC: float = 0.5  # Duration of silence needed for split point
    OVERLAP_DURATION_SEC: float = 2.0  # Overlap between chunks to avoid word cutoffs

    # Whisper model - "auto" selects based on hardware (turbo for GPU, base for CPU)
    DEFAULT_WHISPER_MODEL: str = "auto"

    # Faster-whisper settings
    FASTER_WHISPER_DEVICE: str = "auto"  # "auto", "cuda", "cpu"
    FASTER_WHISPER_COMPUTE_TYPE: str = "float16"  # Blackwell-safe GPU default
    FASTER_WHISPER_VAD_ENABLED: bool = True
    FASTER_WHISPER_VAD_MIN_SILENCE_MS: int = 400
    FASTER_WHISPER_BEAM_SIZE: int = 5
    FASTER_WHISPER_LANGUAGE: str = os.environ.get(
        "ROMANVOICE_FASTER_WHISPER_LANGUAGE",
        "en",
    ).strip() or "en"
    FASTER_WHISPER_CONDITION_ON_PREVIOUS_TEXT: bool = _env_bool(
        "ROMANVOICE_FASTER_WHISPER_CONDITION_ON_PREVIOUS_TEXT",
        False,
    )
    FASTER_WHISPER_COMPRESSION_RATIO_THRESHOLD: float = _env_float(
        "ROMANVOICE_FASTER_WHISPER_COMPRESSION_RATIO_THRESHOLD",
        2.4,
    )
    FASTER_WHISPER_LOG_PROB_THRESHOLD: float = _env_float(
        "ROMANVOICE_FASTER_WHISPER_LOG_PROB_THRESHOLD",
        -1.0,
    )
    FASTER_WHISPER_NO_SPEECH_THRESHOLD: float = _env_float(
        "ROMANVOICE_FASTER_WHISPER_NO_SPEECH_THRESHOLD",
        0.6,
    )
    FASTER_WHISPER_INITIAL_PROMPT: str = (
        "This is English voice dictation for an engineering note. Use normal "
        "punctuation and capitalization. Convert spoken punctuation commands "
        "like comma, period, colon, semicolon, question mark, exclamation point, "
        "and new paragraph into punctuation when they are used as commands. "
        "Preserve the speaker's wording otherwise."
    )
    FASTER_WHISPER_LEGACY_DICTATION_PROMPT: str = (
        "This is English voice dictation. Transcribe with natural punctuation, "
        "sentence capitalization, and paragraph-like clarity while preserving the "
        "speaker's wording."
    )
    FASTER_WHISPER_LIGHT_CLEANUP: bool = True
    TRANSCRIPT_GLOSSARY_ENABLED: bool = True
    TRANSCRIPT_GLOSSARY_FILE: str = field(
        default_factory=lambda: os.path.join(_appdata_dir(), "transcript_glossary.json")
    )

    # Text injection settings
    DEFAULT_AUTO_PASTE: bool = True
    DEFAULT_COPY_CLIPBOARD: bool = False
    TEXT_INJECTION_MODE: str = "unicode"  # "unicode" or "clipboard"
    TEXT_INJECTION_LONG_TEXT_THRESHOLD: int = 5000
    TEXT_INJECTION_KEY_DELAY_MS: int = 0
    TEXT_INJECTION_FOCUS_RECHECK_MS: int = 350
    TEXT_INJECTION_FOCUS_RECHECK_INTERVAL_MS: int = 50
    LIVE_TYPE_ENABLED: bool = True
    LIVE_FINAL_REWRITE_MAX_BACKSPACES: int = 120
    LIVE_FINAL_REWRITE_MAX_BACKSPACE_RATIO: float = 0.35

    # Optional local polishing through an external Ollama install
    POLISH_ENABLED: bool = False
    POLISH_MODEL: str = "gemma3:1b"
    POLISH_WORD_THRESHOLD: int = 30
    POLISH_TIMEOUT_MS: int = 1500
    POLISH_OLLAMA_URL: str = "http://127.0.0.1:11434"

    # Cooperative CUDA behavior. Keeps RomanVoice from competing with exports/games.
    GPU_COOPERATIVE_MODE: bool = True
    GPU_BUSY_UTILIZATION_THRESHOLD: int = 75
    GPU_MIN_FREE_MEMORY_MB: int = 2500
    GPU_WARMUP_MIN_FREE_MEMORY_MB: int = 3500
    GPU_QUERY_TIMEOUT_MS: int = 1000
    GPU_BUSY_RECHECK_MS: int = 1000
    GPU_BUSY_TRANSCRIBE_MAX_WAIT_MS: int = _env_int(
        "ROMANVOICE_GPU_BUSY_TRANSCRIBE_MAX_WAIT_MS",
        5000,
    )
    GPU_BUSY_WARMUP_RETRY_MS: int = 30000
    GPU_COOPERATIVE_MONITOR_MS: int = 30000
    GPU_COOPERATIVE_RELOAD_COOLDOWN_MS: int = 180000
    GPU_COOPERATIVE_UNLOAD_ON_BUSY: bool = _env_bool(
        "ROMANVOICE_GPU_COOPERATIVE_UNLOAD_ON_BUSY",
        False,
    )
    GPU_BUSY_SKIP_STREAMING_PREVIEW: bool = False
    GPU_IGNORE_OWN_CUDA_MEMORY: bool = True
    GPU_BUSY_CPU_FALLBACK_MODEL: str = "base"
    PRELOAD_WHISPER_ON_START: bool = True

    # Local dictation service for PA v2 and future clients.
    SERVICE_ENABLED: bool = _env_bool("ROMANVOICE_SERVICE_ENABLED", True)
    SERVICE_HOST: str = os.environ.get("ROMANVOICE_SERVICE_HOST", "127.0.0.1")
    SERVICE_PORT: int = _env_int("ROMANVOICE_SERVICE_PORT", 8799)
    SERVICE_TOKEN_FILE: str = field(default_factory=_service_token_file)
    SERVICE_TOKEN: str = field(default_factory=_service_token_default)
    SERVICE_MAX_AUDIO_MB: int = _env_int("ROMANVOICE_SERVICE_MAX_AUDIO_MB", 25)
    SERVICE_SAVE_LAST_STREAM_WAV: bool = _env_bool(
        "ROMANVOICE_SERVICE_SAVE_LAST_STREAM_WAV",
        True,
    )
    SERVICE_SAVE_LAST_HTTP_AUDIO: bool = _env_bool(
        "ROMANVOICE_SERVICE_SAVE_LAST_HTTP_AUDIO",
        True,
    )
    SERVICE_HTTP_DIAGNOSTIC_UPLOAD_KEEP_COUNT: int = _env_int(
        "ROMANVOICE_SERVICE_HTTP_DIAGNOSTIC_UPLOAD_KEEP_COUNT",
        10,
    )
    SERVICE_HTTP_DECODE_PROFILE: str = os.environ.get(
        "ROMANVOICE_SERVICE_HTTP_DECODE_PROFILE",
        "http_batch_legacy_context",
    ).strip() or "http_batch_legacy_context"
    SERVICE_HTTP_FASTER_WHISPER_BEAM_SIZE: int = _env_int(
        "ROMANVOICE_SERVICE_HTTP_FASTER_WHISPER_BEAM_SIZE",
        5,
    )
    SERVICE_HTTP_FASTER_WHISPER_LANGUAGE: str = os.environ.get(
        "ROMANVOICE_SERVICE_HTTP_FASTER_WHISPER_LANGUAGE",
        "en",
    ).strip() or "en"
    SERVICE_HTTP_FASTER_WHISPER_CONDITION_ON_PREVIOUS_TEXT: bool = _env_bool(
        "ROMANVOICE_SERVICE_HTTP_FASTER_WHISPER_CONDITION_ON_PREVIOUS_TEXT",
        True,
    )
    SERVICE_HTTP_FASTER_WHISPER_INITIAL_PROMPT: str = os.environ.get(
        "ROMANVOICE_SERVICE_HTTP_FASTER_WHISPER_INITIAL_PROMPT",
        FASTER_WHISPER_LEGACY_DICTATION_PROMPT,
    ).strip() or FASTER_WHISPER_LEGACY_DICTATION_PROMPT
    SERVICE_HTTP_FASTER_WHISPER_VAD_ENABLED: bool = _env_bool(
        "ROMANVOICE_SERVICE_HTTP_FASTER_WHISPER_VAD_ENABLED",
        True,
    )
    SERVICE_HTTP_FASTER_WHISPER_VAD_MIN_SILENCE_MS: int = _env_int(
        "ROMANVOICE_SERVICE_HTTP_FASTER_WHISPER_VAD_MIN_SILENCE_MS",
        400,
    )
    SERVICE_HTTP_LONG_FORM_CHUNK_MIN_SECONDS: float = _env_float(
        "ROMANVOICE_SERVICE_HTTP_LONG_FORM_CHUNK_MIN_SECONDS",
        45.0,
    )
    SERVICE_HTTP_SUSPECT_LOW_DENSITY_MIN_SECONDS: float = _env_float(
        "ROMANVOICE_SERVICE_HTTP_SUSPECT_LOW_DENSITY_MIN_SECONDS",
        120.0,
    )
    SERVICE_HTTP_SUSPECT_MIN_CHARS_PER_MINUTE: float = _env_float(
        "ROMANVOICE_SERVICE_HTTP_SUSPECT_MIN_CHARS_PER_MINUTE",
        180.0,
    )
    SERVICE_HTTP_SUSPECT_MIN_EXPECTED_CHARS: int = _env_int(
        "ROMANVOICE_SERVICE_HTTP_SUSPECT_MIN_EXPECTED_CHARS",
        600,
    )

    # Streaming transcription settings
    STREAMING_ENABLED: bool = True  # Real-time transcription while recording
    STREAMING_CHUNK_DURATION_SEC: float = 2.0  # Process every N seconds
    STREAMING_QUEUE_SIZE: int = 10  # Maximum queued chunks (prevents memory issues)
    STREAMING_BEAM_SIZE: int = 3  # Smaller beam size for faster processing
    STREAMING_VAD_ENABLED: bool = _env_bool("ROMANVOICE_STREAMING_VAD_ENABLED", True)
    PHONE_STREAM_FINAL_PASS_ENABLED: bool = _env_bool(
        "ROMANVOICE_PHONE_STREAM_FINAL_PASS_ENABLED",
        False,
    )
    SHORT_FORM_FINAL_SKIP_MAX_SECONDS: float = 4.0
    SHORT_FORM_FINAL_SKIP_MIN_CHARS: int = 10
    GPU_BUSY_STREAMING_FINAL_SKIP_MAX_SECONDS: float = _env_float(
        "ROMANVOICE_GPU_BUSY_STREAMING_FINAL_SKIP_MAX_SECONDS",
        90.0,
    )
    GPU_BUSY_STREAMING_FINAL_SKIP_MIN_CHARS: int = 10
    LONG_FORM_STREAMING_FALLBACK_MIN_SECONDS: float = 45.0
    LONG_FORM_STREAMING_FALLBACK_MIN_CHARS: int = 300
    LONG_FORM_STREAMING_FALLBACK_MIN_CHAR_DELTA: int = 200
    LONG_FORM_STREAMING_FALLBACK_RATIO: float = 0.80

    # Waveform style settings
    CURRENT_WAVEFORM_STYLE: str = "particle"
    WAVEFORM_STYLE_CONFIGS: Dict[str, Dict] = None

    def __post_init__(self):
        """Initialize computed fields after dataclass creation."""
        for directory in (self.APPDATA_DIR, self.LOCAL_APPDATA_DIR, self.RECORDINGS_FOLDER):
            os.makedirs(directory, exist_ok=True)

        if self.DEFAULT_HOTKEYS is None:
            self.DEFAULT_HOTKEYS = {
                'record_toggle': 'ctrl+space',
                'cancel': 'ctrl+alt+backspace',
                'enable_disable': 'ctrl+alt+shift+space',
            }

        if self.MODEL_VALUE_MAP is None:
            self.MODEL_VALUE_MAP = {
                'Local Whisper': 'local_whisper',
                'API: Whisper': 'api_whisper',
                'API: GPT-4o Transcribe': 'api_gpt4o',
                'API: GPT-4o Mini Transcribe': 'api_gpt4o_mini',
            }

        if self.WHISPER_MODEL_CHOICES is None:
            self.WHISPER_MODEL_CHOICES = [
                # Auto-select based on hardware (turbo for GPU, base for CPU)
                "auto",
                # Standard models
                "tiny", "tiny.en",
                "base", "base.en",
                "small", "small.en",
                "medium", "medium.en",
                "large-v1", "large-v2", "large-v3",
                "turbo",
                # Distil models (faster, English-focused)
                "distil-small.en", "distil-medium.en",
                "distil-large-v2", "distil-large-v3"
            ]

        if self.WAVEFORM_STYLE_CONFIGS is None:
            self.WAVEFORM_STYLE_CONFIGS = {
                'particle': {
                    'max_particles': 150,
                    'emission_rate': 30,
                    'particle_life': 2.0,
                    'gravity': 20,
                    'damping': 0.98,
                    'wind_strength': 5,
                    'audio_response': 1.5,
                    'bg_color': '#0a0a0a',
                    'text_color': '#ffffff',
                    'particle_trail': True,
                    'glow_effect': True,
                    'turbulence_strength': 10,
                    'color_shift_speed': 50
                }
            }

# Global config instance
config = AppConfig()
