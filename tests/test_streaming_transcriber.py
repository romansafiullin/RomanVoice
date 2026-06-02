import numpy as np

from config import config
from services.streaming_transcriber import StreamingTranscriber


class _Segment:
    text = " hello"


class _Model:
    def __init__(self):
        self.seen_kwargs = None

    def transcribe(self, *_args, **kwargs):
        self.seen_kwargs = kwargs
        return [_Segment()], object()


class _Backend:
    def __init__(self):
        self.model = _Model()

    def ensure_loaded(self):
        pass


def test_streaming_transcriber_does_not_emit_after_stop_requested():
    streamer = StreamingTranscriber(_Backend())
    calls = []
    streamer.callback = lambda text, is_final: calls.append((text, is_final))
    streamer.sample_rate = config.WHISPER_TARGET_SAMPLE_RATE
    streamer.is_streaming = True
    streamer._stop_requested = True
    streamer._all_audio_buffer = [np.ones(1600, dtype=np.int16)]

    streamer._process_all_audio()

    assert calls == []
    assert streamer.all_transcriptions == ["hello"]


def test_stop_streaming_disarms_callback_for_late_worker():
    streamer = StreamingTranscriber(_Backend())
    streamer.is_streaming = True
    streamer.callback = lambda _text, _is_final: None
    streamer.all_transcriptions = ["final text"]

    assert streamer.stop_streaming() == "final text"
    assert streamer.callback is None


def test_streaming_transcriber_uses_robust_decode_options():
    backend = _Backend()
    streamer = StreamingTranscriber(backend, vad_filter=True)
    streamer.sample_rate = config.WHISPER_TARGET_SAMPLE_RATE
    streamer._all_audio_buffer = [np.ones(1600, dtype=np.int16)]

    streamer._process_all_audio()

    assert backend.model.seen_kwargs["language"] == "en"
    assert backend.model.seen_kwargs["condition_on_previous_text"] is False
    assert backend.model.seen_kwargs["compression_ratio_threshold"] == 2.4
    assert backend.model.seen_kwargs["log_prob_threshold"] == -1.0
    assert backend.model.seen_kwargs["no_speech_threshold"] == 0.6
    assert backend.model.seen_kwargs["vad_filter"] is True
    assert backend.model.seen_kwargs["vad_parameters"] == {
        "min_silence_duration_ms": config.FASTER_WHISPER_VAD_MIN_SILENCE_MS
    }
