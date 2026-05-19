"""Cooperative CUDA usage checks for background dictation."""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from config import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GPUStatus:
    """Current GPU utilization and memory budget."""

    available: bool
    utilization_percent: Optional[int] = None
    memory_used_mb: Optional[int] = None
    memory_total_mb: Optional[int] = None
    error: Optional[str] = None

    @property
    def memory_free_mb(self) -> Optional[int]:
        if self.memory_used_mb is None or self.memory_total_mb is None:
            return None
        return max(0, self.memory_total_mb - self.memory_used_mb)

    def busy_reason(self) -> Optional[str]:
        """Return a human-readable reason when CUDA should be avoided."""
        if not self.available:
            return None

        reasons = []
        if (
            self.utilization_percent is not None
            and self.utilization_percent >= config.GPU_BUSY_UTILIZATION_THRESHOLD
        ):
            reasons.append(f"utilization {self.utilization_percent}%")

        free_mb = self.memory_free_mb
        if free_mb is not None and free_mb < config.GPU_MIN_FREE_MEMORY_MB:
            reasons.append(f"free memory {free_mb} MB")

        return ", ".join(reasons) if reasons else None


class GPUGuard:
    """Thin nvidia-smi based guard for avoiding heavy shared-GPU moments."""

    def query_status(self) -> GPUStatus:
        """Query the primary NVIDIA GPU.

        Unknown status is treated as non-blocking by callers, because the guard
        is a protective layer rather than a hard dependency.
        """
        try:
            creationflags = 0
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                creationflags = subprocess.CREATE_NO_WINDOW

            result = subprocess.run(
                [
                    "nvidia-smi.exe",
                    "--query-gpu=utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=config.GPU_QUERY_TIMEOUT_MS / 1000,
                creationflags=creationflags,
                check=False,
            )
        except Exception as exc:
            return GPUStatus(available=False, error=str(exc))

        if result.returncode != 0:
            return GPUStatus(
                available=False,
                error=(result.stderr or result.stdout or "").strip(),
            )

        line = (result.stdout or "").strip().splitlines()[0:1]
        if not line:
            return GPUStatus(available=False, error="empty nvidia-smi output")

        try:
            utilization, memory_used, memory_total = [
                int(part.strip()) for part in line[0].split(",")[:3]
            ]
        except Exception as exc:
            return GPUStatus(available=False, error=f"parse failed: {exc}")

        return GPUStatus(
            available=True,
            utilization_percent=utilization,
            memory_used_mb=memory_used,
            memory_total_mb=memory_total,
        )

    def is_cuda_busy(self) -> bool:
        """Return True when configured thresholds say to avoid CUDA."""
        if not config.GPU_COOPERATIVE_MODE:
            return False
        status = self.query_status()
        return bool(status.busy_reason())

    def wait_for_cuda_budget(self, reason: str, max_wait_ms: int) -> bool:
        """Wait until CUDA appears safe to use, or return False on timeout."""
        if not config.GPU_COOPERATIVE_MODE:
            return True

        deadline = time.time() + max(0, max_wait_ms) / 1000
        last_logged_reason = None

        while True:
            status = self.query_status()
            busy_reason = status.busy_reason()
            if not busy_reason:
                if status.available:
                    logger.debug(
                        "CUDA budget available for %s: util=%s%% used=%s/%s MB",
                        reason,
                        status.utilization_percent,
                        status.memory_used_mb,
                        status.memory_total_mb,
                    )
                elif status.error:
                    logger.debug("GPU guard unavailable for %s: %s", reason, status.error)
                return True

            if busy_reason != last_logged_reason:
                logger.info("Deferring %s; CUDA busy: %s", reason, busy_reason)
                last_logged_reason = busy_reason

            if time.time() >= deadline:
                logger.warning("CUDA still busy after waiting for %s: %s", reason, busy_reason)
                return False

            sleep_sec = min(config.GPU_BUSY_RECHECK_MS / 1000, max(0.1, deadline - time.time()))
            time.sleep(sleep_sec)


gpu_guard = GPUGuard()
