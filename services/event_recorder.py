"""
GCS NDJSON event recorder — fallback when LBCF / FSU2A aren't reachable.

Two append paths:

  • Instructions: gs://chiops-fsu100v2-events/instructions/{YYYY-MM-DD}.ndjson
    Each PLACE / CANCEL / REPLACE that would have gone to LBCF.

  • Evaluations: gs://chiops-fsu100v2-events/{YYYY-MM-DD}.ndjson
    Every evaluation envelope that would have gone to FSU2A.
    Same shape FSU2A will consume when it ships — drop-in migration.

Writes are NDJSON tail-append (read-modify-write on the blob, since
GCS objects are immutable individually). To keep this cheap for the
volume we expect (~hundreds of evaluations/sec at peak), we batch
in memory and flush every `flush_interval_s` (default 5s) OR
every `flush_batch_size` lines (default 200). On lifespan shutdown
the recorder flushes whatever's pending.

Same kill-switch pattern: FSU100V2_DISABLE_GCP_IO bypasses everything
(tests record into an in-memory ring buffer instead).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from core.config import get_settings

logger = logging.getLogger(__name__)


def _disabled() -> bool:
    return bool(os.environ.get("FSU100V2_DISABLE_GCP_IO"))


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class _BufferedNdjsonWriter:
    """One writer per blob path. Batches lines, flushes on threshold or timer."""

    def __init__(self, *, blob_path_fn, flush_interval_s: float = 5.0,
                 flush_batch_size: int = 200, max_test_ring: int = 500) -> None:
        self._blob_path_fn = blob_path_fn
        self._flush_interval_s = flush_interval_s
        self._flush_batch_size = flush_batch_size
        self._lock = RLock()
        self._buffer: list[str] = []
        # Test-mode ring buffer so tests can introspect what would have been written.
        self._test_ring: deque[dict] = deque(maxlen=max_test_ring)

    def append(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, default=str, separators=(",", ":"))
        with self._lock:
            self._buffer.append(line)
            self._test_ring.append(payload)
            if len(self._buffer) >= self._flush_batch_size:
                self._flush_locked()

    def test_buffer(self) -> list[dict]:
        with self._lock:
            return list(self._test_ring)

    def reset_for_test(self) -> None:
        with self._lock:
            self._buffer.clear()
            self._test_ring.clear()

    async def flush_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._flush_interval_s)
            except asyncio.CancelledError:
                self._flush_now()
                return
            self._flush_now()

    def _flush_now(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        if _disabled():
            self._buffer.clear()
            return
        bucket_name, blob_name = self._blob_path_fn()
        lines = self._buffer
        self._buffer = []
        try:
            from google.cloud import storage  # type: ignore[import-not-found]

            client = storage.Client()
            blob = client.bucket(bucket_name).blob(blob_name)
            # Read-modify-write — GCS doesn't append. For a tail-write,
            # compose() would be faster, but for the volume here a simple
            # download-append is fine.
            try:
                existing = blob.download_as_text() if blob.exists() else ""
            except Exception:  # noqa: BLE001 — be conservative
                existing = ""
            blob.upload_from_string(
                existing + "\n".join(lines) + "\n",
                content_type="application/x-ndjson",
            )
        except Exception as exc:  # noqa: BLE001
            # Put the lines back so the next flush retries; bounded by max
            # buffer size to avoid unbounded growth.
            logger.warning("NDJSON flush failed (%s) — re-queuing %d lines", exc, len(lines))
            with self._lock:
                self._buffer = lines + self._buffer
                if len(self._buffer) > self._flush_batch_size * 4:
                    self._buffer = self._buffer[-self._flush_batch_size * 4 :]


def _instructions_path() -> tuple[str, str]:
    s = get_settings()
    return s.events_bucket, f"instructions/{_today_iso()}.ndjson"


def _evaluations_path() -> tuple[str, str]:
    s = get_settings()
    return s.events_bucket, f"{_today_iso()}.ndjson"


instructions_writer = _BufferedNdjsonWriter(blob_path_fn=_instructions_path)
evaluations_writer = _BufferedNdjsonWriter(blob_path_fn=_evaluations_path)


def record_instruction(payload: dict[str, Any]) -> None:
    instructions_writer.append(payload)


def record_evaluation_envelope(payload: dict[str, Any]) -> None:
    evaluations_writer.append(payload)


async def run_flush_loops() -> None:
    """Run both writers' flush loops as background tasks."""
    await asyncio.gather(
        instructions_writer.flush_loop(),
        evaluations_writer.flush_loop(),
        return_exceptions=True,
    )


def reset_for_test() -> None:
    instructions_writer.reset_for_test()
    evaluations_writer.reset_for_test()
