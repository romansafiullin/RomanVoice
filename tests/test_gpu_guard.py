from threading import Event

from config import config
from services.gpu_guard import GPUGuard, GPUStatus


def test_busy_reason_ignores_romanvoice_cuda_memory_when_configured(monkeypatch):
    monkeypatch.setattr(config, "GPU_IGNORE_OWN_CUDA_MEMORY", True)
    monkeypatch.setattr(config, "GPU_MIN_FREE_MEMORY_MB", 2500)
    status = GPUStatus(
        available=True,
        utilization_percent=0,
        memory_used_mb=6900,
        memory_total_mb=8192,
        self_memory_used_mb=5600,
    )

    assert status.memory_free_mb == 1292
    assert status.effective_memory_free_mb == 6892
    assert status.busy_reason() is None


def test_busy_reason_still_counts_external_cuda_memory(monkeypatch):
    monkeypatch.setattr(config, "GPU_IGNORE_OWN_CUDA_MEMORY", True)
    monkeypatch.setattr(config, "GPU_MIN_FREE_MEMORY_MB", 2500)
    status = GPUStatus(
        available=True,
        utilization_percent=0,
        memory_used_mb=6900,
        memory_total_mb=8192,
        self_memory_used_mb=1000,
    )

    assert status.effective_memory_free_mb == 2292
    assert "free memory 2292 MB after excluding RomanVoice 1000 MB" in (
        status.busy_reason() or ""
    )


def test_busy_reason_uses_raw_memory_when_self_memory_ignore_is_disabled(monkeypatch):
    monkeypatch.setattr(config, "GPU_IGNORE_OWN_CUDA_MEMORY", False)
    monkeypatch.setattr(config, "GPU_MIN_FREE_MEMORY_MB", 2500)
    status = GPUStatus(
        available=True,
        utilization_percent=0,
        memory_used_mb=6900,
        memory_total_mb=8192,
        self_memory_used_mb=5600,
    )

    assert status.effective_memory_free_mb == 1292
    assert status.busy_reason() == "free memory 1292 MB"


def test_wait_for_cuda_budget_returns_false_when_canceled(monkeypatch):
    monkeypatch.setattr(config, "GPU_COOPERATIVE_MODE", True)
    guard = GPUGuard()
    cancel_event = Event()
    cancel_event.set()
    queries = []
    monkeypatch.setattr(
        guard,
        "query_status",
        lambda: queries.append(True) or GPUStatus(available=True),
    )

    assert guard.wait_for_cuda_budget(
        "final transcription",
        max_wait_ms=1000,
        cancel_event=cancel_event,
    ) is False
    assert queries == []
