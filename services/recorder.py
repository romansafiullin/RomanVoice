"""
Audio recording functionality for the OpenWhisper application.
"""
import sounddevice as sd
import wave
import threading
import logging
import numpy as np
import time
import os

from typing import Callable, List, Optional, Tuple
from config import config

logger = logging.getLogger(__name__)

AudioLevelCallback = Callable[[float], None]


class AudioRecorder:
    """Handles audio recording using SoundDevice."""

    @staticmethod
    def get_input_devices() -> List[Tuple[int, str]]:
        """Get list of available audio input devices.

        Returns:
            List of tuples (device_id, device_name) for devices with input channels.
        """
        devices = []
        try:
            all_devices = sd.query_devices()
            for i, device in enumerate(all_devices):
                if device['max_input_channels'] > 0:
                    devices.append((i, device['name']))
        except Exception as e:
            logger.error(f"Failed to enumerate audio devices: {e}")
        return devices

    @staticmethod
    def _resolve_input_device(device_id: Optional[int]) -> Optional[int]:
        """Resolve the input device used for recording.

        When the user chooses "System Default", PortAudio often resolves to an
        older MME endpoint on Windows. Prefer the matching WASAPI endpoint when
        present because it is the modern Windows audio path and usually behaves
        better for desktop dictation.
        """
        if device_id is not None or not config.PREFER_WASAPI_INPUT:
            return device_id

        try:
            default_input = sd.default.device[0]
            if default_input is None or default_input < 0:
                return device_id

            devices = sd.query_devices()
            hostapis = sd.query_hostapis()
            default_device = devices[default_input]
            default_name = default_device["name"]

            for index, candidate in enumerate(devices):
                if candidate.get("max_input_channels", 0) <= 0:
                    continue
                hostapi_name = hostapis[candidate["hostapi"]]["name"]
                if hostapi_name != "Windows WASAPI":
                    continue
                if candidate["name"] == default_name:
                    logger.info(
                        "Using WASAPI input device %s for default microphone %r",
                        index,
                        default_name,
                    )
                    return index
        except Exception as exc:
            logger.debug(f"Could not resolve WASAPI input device: {exc}")

        return device_id

    def __init__(self, device_id: Optional[int] = None):
        """Initialize the audio recorder.

        Args:
            device_id: Optional device ID for input. None uses system default.
        """
        self.requested_device_id = device_id
        self.device_id = self._resolve_input_device(device_id)
        self.is_recording = False
        self.frames: List[bytes] = []
        self.stream: Optional[sd.InputStream] = None
        self.recording_thread: Optional[threading.Thread] = None
        self._stop_requested: bool = False
        self._post_roll_until: float = 0.0
        self._recording_complete_event = threading.Event()
        self._stream_started_event = threading.Event()
        self._stream_error: Optional[str] = None

        # Audio settings from config
        self.chunk = config.CHUNK_SIZE
        self.dtype = config.AUDIO_FORMAT
        self.channels, self.rate = self._resolve_audio_settings()

        # Audio level callback
        self.audio_level_callback: Optional[AudioLevelCallback] = None

        # Streaming transcription callback
        self.streaming_callback: Optional[Callable[[np.ndarray], None]] = None

        # Audio level calculation
        self._current_audio_level = 0.0
        self._level_smoothing = config.WAVEFORM_LEVEL_SMOOTHING  # Smoothing factor for level changes

        # Thread safety for callback
        self._callback_lock = threading.Lock()

        logger.info(
            "Audio recorder initialized (requested_device=%s, resolved_device=%s, channels=%s, rate=%s)",
            self.requested_device_id,
            self.device_id,
            self.channels,
            self.rate,
        )

    def _resolve_audio_settings(self) -> Tuple[int, int]:
        """Resolve channel count and sample rate for the selected input device."""
        channels = config.CHANNELS
        rate = config.SAMPLE_RATE

        try:
            device_info = None
            if self.device_id is not None:
                device_info = sd.query_devices(self.device_id)
            else:
                default_input = sd.default.device[0]
                if default_input is not None and default_input >= 0:
                    device_info = sd.query_devices(default_input)

            if device_info:
                max_input_channels = int(device_info.get("max_input_channels", channels) or channels)
                if max_input_channels > 0:
                    channels = max(1, min(channels, max_input_channels))

                default_samplerate = device_info.get("default_samplerate")
                if default_samplerate:
                    rate = int(default_samplerate)
        except Exception as exc:
            logger.debug(f"Could not resolve native audio settings: {exc}")

        return channels, rate

    @staticmethod
    def _initialize_com_for_audio_thread() -> bool:
        """Initialize COM for Windows audio APIs used inside worker threads."""
        if os.name != "nt":
            return False

        try:
            import ctypes

            coinit_multithreaded = 0x0
            rpc_e_changed_mode = 0x80010106
            hr = ctypes.windll.ole32.CoInitializeEx(None, coinit_multithreaded)
            unsigned_hr = hr & 0xFFFFFFFF

            if hr in (0, 1):  # S_OK or S_FALSE; both require CoUninitialize.
                return True

            if unsigned_hr == rpc_e_changed_mode:
                logger.debug("COM already initialized with a different threading model")
                return False

            logger.debug(f"CoInitializeEx returned HRESULT 0x{unsigned_hr:08x}")
        except Exception as exc:
            logger.debug(f"Could not initialize COM for audio thread: {exc}")

        return False

    def set_audio_level_callback(self, callback: AudioLevelCallback):
        """Set callback function for real-time audio level updates.

        Args:
            callback: Function that will be called with audio level (0.0 to 1.0)
        """
        self.audio_level_callback = callback

    def set_streaming_callback(self, callback: Callable[[np.ndarray], None]):
        """Set callback function for real-time streaming transcription.

        Args:
            callback: Function that will be called with audio chunks (NumPy arrays)
        """
        self.streaming_callback = callback

    def start_recording(self) -> bool:
        """Start audio recording.

        Returns:
            True if recording started successfully, False otherwise.
        """
        if self.is_recording:
            logger.warning("Recording already in progress")
            return False

        try:
            # Reset completion signal for this session
            self._recording_complete_event = threading.Event()
            self._stream_started_event = threading.Event()
            self._stream_error = None

            self.clear_recording_data()

            # Delete old audio file if it exists
            import os
            if os.path.exists(config.RECORDED_AUDIO_FILE):
                try:
                    os.remove(config.RECORDED_AUDIO_FILE)
                    logger.info(f"Deleted old audio file: {config.RECORDED_AUDIO_FILE}")
                except Exception as e:
                    logger.warning(f"Could not delete old audio file: {e}")

            self.is_recording = True
            self._stop_requested = False
            self._post_roll_until = 0.0

            # Start recording in a separate thread
            self.recording_thread = threading.Thread(target=self._record_audio, daemon=True)
            self.recording_thread.start()

            if not self._stream_started_event.wait(timeout=1.5):
                self._stream_error = "Audio stream did not start within 1.5 seconds"

            if self._stream_error:
                self.is_recording = False
                self._stop_requested = True
                self._recording_complete_event.set()
                if self.recording_thread and self.recording_thread.is_alive():
                    self.recording_thread.join(timeout=0.5)
                logger.error(f"Recording failed to start: {self._stream_error}")
                return False

            logger.info("Recording started - frames cleared, old file removed")
            return True

        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            self.is_recording = False
            return False

    def stop_recording(self) -> bool:
        """Stop audio recording.

        Returns:
            True if recording stopped successfully, False otherwise.
        """
        if not self.is_recording:
            logger.warning("No recording in progress")
            return False

        try:
            # Request stop and allow a short post-roll to capture trailing speech
            self._stop_requested = True
            self._post_roll_until = time.time() + (config.POST_ROLL_MS / 1000.0)

            # Don't wait for recording thread to finish - let post-roll happen in background
            # The thread will naturally finish after the post-roll period
            logger.info("Recording stop requested, post-roll continuing in background")
            return True

        except Exception as e:
            logger.error(f"Failed to stop recording: {e}")
            return False

    def wait_for_stop_completion(self, timeout: float = None) -> bool:
        """Wait for the recorder thread to finish post-roll capture.

        Args:
            timeout: Optional timeout in seconds. Defaults to post-roll plus grace.

        Returns:
            True if the recorder finished within the timeout, False otherwise.
        """
        if not self.recording_thread or not self.recording_thread.is_alive():
            return True

        # Give the thread enough time for post-roll plus a small buffer
        default_timeout = (config.POST_ROLL_MS + config.POST_ROLL_FINALIZE_GRACE_MS) / 1000.0
        wait_timeout = timeout if timeout is not None else default_timeout

        finished = self._recording_complete_event.wait(wait_timeout)
        if not finished:
            logger.warning("Recording thread did not finish during post-roll wait; proceeding with available audio")
        return finished

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status):
        """Callback function for sounddevice to process incoming audio data.

        Args:
            indata: Input audio data as numpy array
            frames: Number of frames
            time_info: Time information
            status: Stream status
        """
        if status:
            logger.warning(f"Audio stream status: {status}")

        try:
            # Thread-safe frame appending
            with self._callback_lock:
                # Store as bytes for WAV file compatibility
                self.frames.append(indata.copy().tobytes())

                # Calculate audio level for waveform display
                if self.audio_level_callback:
                    self._calculate_and_report_level(indata.copy())

                # Feed to streaming transcriber (non-blocking)
                if self.streaming_callback:
                    try:
                        self.streaming_callback(indata.copy())
                    except Exception as stream_err:
                        logger.debug(f"Streaming callback error: {stream_err}")

        except Exception as e:
            logger.error(f"Error in audio callback: {e}")

    def _record_audio(self):
        """Record audio data in a separate thread until recording is stopped."""
        com_initialized = self._initialize_com_for_audio_thread()
        try:
            # Create input stream with callback
            self.stream = sd.InputStream(
                device=self.device_id,
                samplerate=self.rate,
                channels=self.channels,
                dtype=self.dtype,
                blocksize=self.chunk,
                callback=self._audio_callback
            )

            # Start the stream
            self.stream.start()
            self._stream_started_event.set()
            logger.info(
                "Audio stream started (device=%s, channels=%s, rate=%s, blocksize=%s)",
                self.device_id,
                self.channels,
                self.rate,
                self.chunk,
            )

            # Wait until stop is requested and post-roll window has elapsed
            while True:
                time.sleep(0.01)  # Small sleep to avoid busy-waiting

                # Evaluate exit condition
                if self._stop_requested and time.time() >= self._post_roll_until:
                    break

        except Exception as e:
            self._stream_error = str(e)
            self._stream_started_event.set()
            logger.error(f"Error opening audio stream: {e}")
        finally:
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                    logger.info("Audio stream stopped and closed")
                except Exception as e:
                    logger.error(f"Error closing audio stream: {e}")
            # Mark not recording and clear internal flags
            self.is_recording = False
            self._stop_requested = False
            self._post_roll_until = 0.0
            self.stream = None
            self.recording_thread = None
            # Signal any waiters that recording is fully finished
            self._recording_complete_event.set()

            if com_initialized:
                try:
                    import ctypes

                    ctypes.windll.ole32.CoUninitialize()
                except Exception as exc:
                    logger.debug(f"Could not uninitialize COM for audio thread: {exc}")

    def _calculate_and_report_level(self, audio_data: np.ndarray):
        """Calculate audio level from numpy audio data and report it via callback.

        Args:
            audio_data: Audio data as numpy array
        """
        try:
            # Calculate RMS level
            if len(audio_data) > 0:
                # Normalize to 0.0-1.0 range
                if self.dtype == np.int16:
                    # For 16-bit audio, max value is 32767
                    rms_level = np.sqrt(np.mean(audio_data.astype(np.float64) ** 2)) / 32767.0
                elif self.dtype == np.float32:
                    # For float32, assume range is -1.0 to 1.0
                    rms_level = np.sqrt(np.mean(audio_data ** 2))
                else:
                    return  # Unsupported format

                # Apply smoothing
                self._current_audio_level = (
                    self._level_smoothing * self._current_audio_level +
                    (1.0 - self._level_smoothing) * rms_level
                )

                # Clamp to valid range
                self._current_audio_level = max(0.0, min(1.0, self._current_audio_level))

                # Call the callback with the calculated level
                if self.audio_level_callback:
                    self.audio_level_callback(self._current_audio_level)

        except Exception as e:
            logger.debug(f"Error calculating audio level: {e}")

    def save_recording(self, filename: str = None) -> bool:
        """Save the recorded audio frames to a WAV file.

        Args:
            filename: Output filename. Uses config default if None.

        Returns:
            True if saved successfully, False otherwise.
        """
        if not self.frames:
            logger.warning("No audio data to save")
            return False

        filename = filename or config.RECORDED_AUDIO_FILE

        # Take a snapshot of frames while holding the callback lock to avoid races
        with self._callback_lock:
            frames_to_write = list(self.frames)

        frame_count = len(frames_to_write)
        total_bytes = sum(len(frame) for frame in frames_to_write)

        # Add a bit of trailing silence to reduce ASR truncation at the end
        padding_bytes = b''
        if config.END_PADDING_MS > 0:
            padding_samples = int(self.rate * (config.END_PADDING_MS / 1000.0))
            if padding_samples > 0:
                silence_shape = (padding_samples, self.channels) if self.channels > 1 else (padding_samples,)
                padding_bytes = np.zeros(silence_shape, dtype=self.dtype).tobytes()
                total_bytes += len(padding_bytes)

        try:
            # Create a temporary file first, then rename for atomic operation
            import tempfile
            import os
            temp_fd, temp_path = tempfile.mkstemp(suffix='.wav', dir=os.path.dirname(filename))

            try:
                with os.fdopen(temp_fd, 'wb') as temp_file:
                    with wave.open(temp_file, 'wb') as wf:
                        wf.setnchannels(self.channels)
                        # Get sample width from numpy dtype
                        wf.setsampwidth(np.dtype(self.dtype).itemsize)
                        wf.setframerate(self.rate)
                        wf.writeframes(b''.join(frames_to_write) + padding_bytes)

                # Atomically replace the old file
                if os.path.exists(filename):
                    os.remove(filename)
                os.rename(temp_path, filename)

                import time
                if padding_bytes:
                    logger.info(f"Appended {config.END_PADDING_MS}ms of silence to protect the tail of the recording")
                logger.info(f"Audio saved to {filename} at {time.strftime('%Y-%m-%d %H:%M:%S')} - {frame_count} frames, {total_bytes} bytes, {self.get_recording_duration():.2f}s")
                return True

            except Exception as e:
                # Clean up temp file on error
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise

        except Exception as e:
            logger.error(f"Failed to save audio to {filename}: {e}")
            return False

    def get_recording_duration(self) -> float:
        """Get the duration of the current recording in seconds.

        Returns:
            Duration in seconds, or 0 if no recording data.
        """
        if not self.frames:
            return 0.0

        with self._callback_lock:
            frames_snapshot = list(self.frames)

        bytes_per_sample = np.dtype(self.dtype).itemsize
        total_bytes = sum(len(frame) for frame in frames_snapshot)
        if bytes_per_sample <= 0 or self.channels <= 0:
            return 0.0

        total_samples = total_bytes / (bytes_per_sample * self.channels)
        return total_samples / self.rate

    def get_recording_signal_metrics(self) -> dict:
        """Return simple signal metrics for the captured frames."""
        with self._callback_lock:
            frames_snapshot = list(self.frames)

        if not frames_snapshot:
            return {"rms": 0.0, "peak": 0, "samples": 0}

        try:
            audio = np.frombuffer(b"".join(frames_snapshot), dtype=self.dtype)
            if audio.size == 0:
                return {"rms": 0.0, "peak": 0, "samples": 0}

            rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
            peak = int(np.max(np.abs(audio.astype(np.int32))))
            return {"rms": rms, "peak": peak, "samples": int(audio.size)}
        except Exception as exc:
            logger.warning(f"Failed to calculate recording metrics: {exc}")
            return {"rms": 0.0, "peak": 0, "samples": 0}

    def has_recording_data(self) -> bool:
        """Check if there is recorded audio data available.

        Returns:
            True if recording data is available, False otherwise.
        """
        return bool(self.frames)

    def clear_recording_data(self):
        """Clear the recorded audio data."""
        with self._callback_lock:
            old_frame_count = len(self.frames)
            self.frames = []

        logger.info(f"Cleared recording data. Old frame count: {old_frame_count}")

    def cleanup(self):
        """Clean up audio resources."""
        try:
            if self.is_recording:
                self.stop_recording()
                # Give the thread a moment to finish, but don't wait indefinitely
                if self.recording_thread and self.recording_thread.is_alive():
                    # Wait briefly for thread to finish, but don't block forever
                    self.recording_thread.join(timeout=0.5)
                    if self.recording_thread.is_alive():
                        logger.warning("Recording thread did not finish during cleanup timeout")

            # Close stream if still open
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    pass  # Ignore errors during cleanup
                self.stream = None

            # SoundDevice doesn't require explicit termination like PyAudio
            logger.info("Audio recorder cleaned up")

        except Exception as e:
            # Don't log errors during shutdown - they're often harmless
            logger.debug(f"Error during audio recorder cleanup: {e}")
