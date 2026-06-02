from types import SimpleNamespace

from transcriber.local_backend import LocalWhisperBackend


class _Segment:
    text = " hello"


class _Model:
    def __init__(self):
        self.seen_kwargs = None

    def transcribe(self, *_args, **kwargs):
        self.seen_kwargs = kwargs
        return [_Segment()], SimpleNamespace(language="en", language_probability=1.0)


def test_local_backend_uses_robust_decode_options():
    backend = LocalWhisperBackend(model_name="base", autoload=False)
    backend.model = _Model()

    assert backend.transcribe("sample.wav") == "Hello."

    kwargs = backend.model.seen_kwargs
    assert kwargs["language"] == "en"
    assert kwargs["condition_on_previous_text"] is False
    assert kwargs["compression_ratio_threshold"] == 2.4
    assert kwargs["log_prob_threshold"] == -1.0
    assert kwargs["no_speech_threshold"] == 0.6
    assert kwargs["vad_filter"] is True
    assert kwargs["vad_parameters"] == {"min_silence_duration_ms": 400}
