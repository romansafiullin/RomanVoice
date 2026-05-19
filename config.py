"""
Configuration constants for the OpenWhisper application.
"""
import os
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


def _start_hidden_default() -> bool:
    # The full window should only appear from an explicit UI/debug launcher.
    return not _env_bool("ROMANVOICE_FORCE_SHOW", False)


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
    # Hotkey watchdog: detects sleep/resume gaps; periodic refresh re-registers the hook
    ENABLE_GLOBAL_HOTKEYS: bool = _env_bool("ROMANVOICE_ENABLE_GLOBAL_HOTKEYS", True)
    HOTKEY_BACKEND: str = "win32"
    HOTKEY_WATCHDOG_INTERVAL_MS: int = 10_000
    HOTKEY_SLEEP_GAP_THRESHOLD_SEC: float = 30.0
    HOTKEY_HOOK_REFRESH_INTERVAL_MS: int = 5 * 60 * 1000
    COMPACT_STATUS_OVERLAY: bool = True
    COMPACT_OVERLAY_LIVE_PREVIEW: bool = True
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
    FASTER_WHISPER_CONDITION_ON_PREVIOUS_TEXT: bool = True
    FASTER_WHISPER_INITIAL_PROMPT: str = (
        "This is English voice dictation. Transcribe with natural punctuation, "
        "sentence capitalization, and paragraph-like clarity while preserving the "
        "speaker's wording."
    )
    FASTER_WHISPER_LIGHT_CLEANUP: bool = True

    # Text injection settings
    TEXT_INJECTION_MODE: str = "unicode"  # "unicode" or "clipboard"
    TEXT_INJECTION_LONG_TEXT_THRESHOLD: int = 5000
    TEXT_INJECTION_KEY_DELAY_MS: int = 0
    LIVE_TYPE_ENABLED: bool = True

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
    GPU_BUSY_TRANSCRIBE_MAX_WAIT_MS: int = 30000
    GPU_BUSY_WARMUP_RETRY_MS: int = 30000
    GPU_COOPERATIVE_MONITOR_MS: int = 30000
    GPU_COOPERATIVE_RELOAD_COOLDOWN_MS: int = 180000
    GPU_BUSY_SKIP_STREAMING_PREVIEW: bool = True
    GPU_BUSY_CPU_FALLBACK_MODEL: str = "base"
    PRELOAD_WHISPER_ON_START: bool = True

    # Streaming transcription settings
    STREAMING_ENABLED: bool = True  # Real-time preview while recording
    STREAMING_CHUNK_DURATION_SEC: float = 2.0  # Process every N seconds
    STREAMING_QUEUE_SIZE: int = 10  # Maximum queued chunks (prevents memory issues)
    STREAMING_BEAM_SIZE: int = 3  # Smaller beam size for faster processing

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
