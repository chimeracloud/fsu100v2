"""
Dispatcher — three outputs per evaluation.

For each `EvaluationResult` produced by a plugin:

  1. **Instructions → LBCF**
     POST each `Instruction` envelope to `live_betting_control` URL
     discovered via the Source Manifest. If LBCF is unreachable
     (manifest entry null OR HTTP error), record to GCS NDJSON AND
     raise the `live_betting_control_unreachable` warning so the
     operator sees it in /admin/status.warnings.

  2. **Evaluation event → FSU2A**
     POST the full evaluation envelope (per Bible §20) to FSU2A's
     `/api/events`. Same fallback behaviour.

  3. **Local SSE → /stream/evaluations**
     Broadcast for the CST portal to render live. This is in-memory
     pub/sub via core.state.app_state.broadcast — fast, non-blocking,
     non-durable.

Auth: service-to-service IAM ID tokens. We mint a token targeting
the destination URL on each request — tokens are cached for ~50 minutes
to avoid hammering the metadata server.

DRY_RUN: when `Settings.dry_run=true`, output (1) and (2) are SKIPPED
(only the local SSE broadcast fires) and a single
`dry_run_dispatch_skipped` activity row is logged. Used in Phase 5
parity testing.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import httpx

from core.config import get_settings
from core.plugin_api import EvaluationResult
from core.state import app_state

from .event_recorder import record_evaluation_envelope, record_instruction

logger = logging.getLogger(__name__)


# ── ID-token cache ──────────────────────────────────────────────────────


def _disabled() -> bool:
    return bool(os.environ.get("FSU100V2_DISABLE_GCP_IO"))


_id_token_cache: dict[str, tuple[str, float]] = {}
_ID_TOKEN_TTL_S = 50 * 60   # mint a fresh token every 50min (Google tokens last 60min)


def _fetch_id_token(target_url: str) -> str | None:
    """Return a Bearer ID token for `target_url`. None if unavailable."""
    if _disabled():
        return None
    now = time.time()
    cached = _id_token_cache.get(target_url)
    if cached and (now - cached[1]) < _ID_TOKEN_TTL_S:
        return cached[0]
    try:
        from google.oauth2 import id_token  # type: ignore[import-not-found]
        from google.auth.transport import requests as g_requests  # type: ignore[import-not-found]

        token = id_token.fetch_id_token(g_requests.Request(), target_url)
        _id_token_cache[target_url] = (token, now)
        return token
    except Exception as exc:  # noqa: BLE001
        logger.warning("ID-token fetch failed for %s: %s", target_url, exc)
        return None


def reset_token_cache_for_test() -> None:
    _id_token_cache.clear()


# ── instruction envelope construction ───────────────────────────────────


def _instruction_envelope(
    result: EvaluationResult,
    instruction,
    settings,
    source_id: str,
    source_type: str,
    snapshot_ts: str | None,
    market_summary: dict,
) -> dict[str, Any]:
    """Build one wire envelope per §8 of the build brief."""
    seq = app_state.instruction_count + 1
    instruction_id = (
        f"instr_horseracing_{datetime.now(timezone.utc).strftime('%Y%m%d')}_{seq:06d}"
    )
    return {
        "instruction_id": instruction_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "product": "horse_racing",
        "engine": "fsu100v2",
        "engine_version": "0.3.0-phase3",
        "session_id": f"session_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
        "source": {
            "source_id": source_id,
            "source_type": source_type,
            "snapshot_ts": snapshot_ts,
        },
        "plugin": {
            "id": result.plugin_id,
            "version": result.plugin_version,
        },
        "market": {
            "market_id": market_summary.get("market_id") or result.market_id,
            "event_id": market_summary.get("event_id"),
            "event_name": market_summary.get("venue") or "",
            "market_name": market_summary.get("name"),
            "scheduled_start": market_summary.get("market_time"),
            "venue": market_summary.get("venue"),
            "country_code": market_summary.get("country_code"),
            "market_type": market_summary.get("market_type"),
        },
        "instruction": {
            "action": instruction.action,
            "side": instruction.side,
            "selection_id": instruction.selection_id,
            "selection_name": instruction.selection_name,
            "price": instruction.price,
            "size": instruction.size,
            "persistence_type": instruction.persistence_type,
            "customer_order_ref": instruction.customer_order_ref or instruction_id,
            "customer_strategy_ref": instruction.customer_strategy_ref or result.plugin_id,
        },
        "decision": instruction.decision,
    }


def _event_envelope_for_fsu2a(envelope: dict, evaluation_payload: dict) -> dict[str, Any]:
    """Wrap evaluation in the universal event envelope (Bible §20)."""
    return {
        "envelope": {
            "source": "fsu100v2",
            "event_type": envelope.get("event_type", "instruction_issued"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "1.0",
        },
        "payload": evaluation_payload,
    }


# ── POST helpers (best-effort, fallback to NDJSON) ──────────────────────


_WARN_LBCF = "live_betting_control_unreachable: instructions queued to GCS"
_WARN_FSU2A = "fsu2a_unreachable: evaluation events queued to GCS"


async def _post_with_fallback(
    *, target_label: str, base_url: str | None, path: str, payload: dict,
    fallback_record_fn, warning_text: str,
    timeout_s: float = 4.0,
) -> bool:
    """Try POST; on absence/failure, fallback-record + set warning. Returns True on real POST success."""
    if not base_url:
        fallback_record_fn(payload)
        app_state.add_warning(warning_text)
        return False

    headers: dict[str, str] = {"Content-Type": "application/json"}
    token = _fetch_id_token(base_url)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = base_url.rstrip("/") + path
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(url, json=payload, headers=headers)
        if r.status_code >= 400:
            logger.warning("%s POST %s returned %s", target_label, url, r.status_code)
            fallback_record_fn(payload)
            app_state.add_warning(warning_text)
            return False
        # Real success — clear the warning if it was set.
        app_state.clear_warning(warning_text)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s POST %s failed: %s", target_label, url, exc)
        fallback_record_fn(payload)
        app_state.add_warning(warning_text)
        return False


# ── public dispatch ─────────────────────────────────────────────────────


async def dispatch(
    result: EvaluationResult,
    *,
    market_summary: dict,
    source_id: str,
    source_type: str,
    snapshot_ts: str | None,
) -> dict[str, Any]:
    """Send `result` to all three outputs. Returns a summary dict for tests."""
    settings = get_settings()
    fallback_url_lbcf = settings.fallback_urls.live_betting_control
    fallback_url_fsu2a = settings.fallback_urls.fsu2a

    summary: dict[str, Any] = {
        "instructions_emitted": 0,
        "lbcf_posted": 0,
        "fsu2a_posted": 0,
        "broadcast": 0,
        "dry_run": settings.dry_run,
    }

    # ── 1. LBCF (instructions) — one POST per Instruction ──────────────
    instruction_envelopes: list[dict[str, Any]] = []
    for instr in result.instructions:
        env = _instruction_envelope(
            result, instr, settings, source_id, source_type, snapshot_ts, market_summary,
        )
        instruction_envelopes.append(env)
        app_state.instruction_count += 1

    summary["instructions_emitted"] = len(instruction_envelopes)

    if instruction_envelopes and not settings.dry_run:
        # Issue all instructions concurrently — they're independent.
        post_results = await asyncio.gather(
            *[
                _post_with_fallback(
                    target_label="LBCF",
                    base_url=fallback_url_lbcf or None,
                    path="/api/instructions",
                    payload=env,
                    fallback_record_fn=record_instruction,
                    warning_text=_WARN_LBCF,
                )
                for env in instruction_envelopes
            ],
            return_exceptions=False,
        )
        summary["lbcf_posted"] = sum(1 for ok in post_results if ok)
    elif instruction_envelopes and settings.dry_run:
        # DRY_RUN — still write to NDJSON so the parity test can read.
        for env in instruction_envelopes:
            record_instruction(env)
        app_state.add_activity(
            "dry_run_dispatch_skipped",
            f"{len(instruction_envelopes)} instructions recorded to GCS, not POSTed",
        )

    # Track instructions per plugin.
    if instruction_envelopes:
        app_state.instructions_by_plugin[result.plugin_id] = (
            app_state.instructions_by_plugin.get(result.plugin_id, 0)
            + len(instruction_envelopes)
        )
        for env in instruction_envelopes:
            app_state.recent_instructions.append(env)

    # ── 2. FSU2A (evaluation envelope) ────────────────────────────────
    eval_payload = {
        "market_id": result.market_id,
        "plugin_id": result.plugin_id,
        "plugin_version": result.plugin_version,
        "ts": result.ts.isoformat() if hasattr(result.ts, "isoformat") else str(result.ts),
        "skip_reason": result.skip_reason,
        "evidence": result.evidence,
        "instructions": [_serialise_instruction(i) for i in result.instructions],
        "instruction_ids": [env["instruction_id"] for env in instruction_envelopes],
    }
    fsu2a_env = {
        "envelope": {
            "source": "fsu100v2",
            "event_type": "evaluation_completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "1.0",
        },
        "payload": eval_payload,
    }
    if not settings.dry_run:
        ok = await _post_with_fallback(
            target_label="FSU2A",
            base_url=fallback_url_fsu2a or None,
            path="/api/events",
            payload=fsu2a_env,
            fallback_record_fn=record_evaluation_envelope,
            warning_text=_WARN_FSU2A,
        )
        summary["fsu2a_posted"] = 1 if ok else 0
    else:
        record_evaluation_envelope(fsu2a_env)

    # ── 3. Local SSE broadcast for the portal ─────────────────────────
    sse_payload = {
        "event": "evaluation",
        "ts": datetime.now(timezone.utc).isoformat(),
        "market_id": result.market_id,
        "market_name": market_summary.get("name"),
        "venue": market_summary.get("venue"),
        "plugin_id": result.plugin_id,
        "rule_applied": (result.evidence or {}).get("rule_applied"),
        "skip_reason": result.skip_reason,
        "instructions": [_serialise_instruction(i) for i in result.instructions],
        "pipeline": (result.evidence or {}).get("pipeline", []),
    }
    await app_state.broadcast("evaluations", sse_payload)
    summary["broadcast"] = 1

    # Counters.
    app_state.evaluation_count += 1
    app_state.evaluations_by_plugin[result.plugin_id] = (
        app_state.evaluations_by_plugin.get(result.plugin_id, 0) + 1
    )
    if not result.instructions:
        app_state.skip_count += 1

    return summary


def _serialise_instruction(instr) -> dict[str, Any]:
    """Instruction dataclass → JSON-safe dict."""
    d = asdict(instr)
    return d
