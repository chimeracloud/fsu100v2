# FSU100V2 — Horse Racing Lay Engine

> Born from complexity. Engineered for certainty.

Chimera's pure horse-racing decision engine. Consumes market data from FSU1B, evaluates through pluggable strategies, emits instructions to the Live Betting Control FSU.

**NO Betfair imports. NO credentials. NO Secret Manager reads.** This engine never talks to Betfair. It reads market data from FSU1B (the Betfair edge), runs each market through plugin strategies, and emits instructions. Order placement is LBCF's job; FSU100V2 never places a bet.

## Scope

| Layer | Direction | What |
|---|---|---|
| Market data IN | from FSU1B | SSE per-sport (`/stream/horse-racing`) + snapshot bootstrap |
| Strategy | inside | One or more plugins under `plugins/<id>/` implementing `StrategyPlugin` |
| Instructions OUT | to LBCF | Per-evaluation envelopes (fallback: GCS NDJSON) |
| Events OUT | to FSU2A | Universal envelope per Bible §20 (fallback: GCS NDJSON) |
| Live feed OUT | to portal | `/stream/evaluations` SSE — every decision rendered in real-time |
| Out of scope | — | Betfair stream / REST / orders / account / settlement / recording / calculation |

Full brief: `~/Downloads/FSU100V2_Build_Brief_for_Claude_4.8.md`.

## Phases (CHI-POL-008)

| Phase | Status | What |
|---|---|---|
| 1 — Shell | in progress | Standard FSU shell, GCS config, Source Manifest, Pub/Sub envelopes — NO Betfair, NO plugin yet |
| 2 — First plugin | pending | Port CLEv2 evaluator into `plugins/mark_6_rules_v1/`; plugin lifecycle endpoints |
| 3 — SSE consumer + dispatch | pending | Subscribe to FSU1B; dispatch instructions to LBCF / events to FSU2A / SSE to portal |
| 4 — Integration | pending | CST portal wiring, DEPLOY.md, full activity feed |
| 5 — Parity + cutover | pending | DRY_RUN parity vs CLEv2 over a race afternoon; live cutover |

## Running locally (Phase 1)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export FSU100V2_DISABLE_GCP_IO=1
uvicorn main:app --reload --port 8080
```

```bash
curl localhost:8080/health
curl localhost:8080/ready
curl localhost:8080/info
curl localhost:8080/metrics
curl localhost:8080/admin/status
curl localhost:8080/admin/config
```

## Tests

```bash
pytest -q
```

## Naming

- Repo: `chimeracloud/fsu100v2`
- Cloud Run service: `fsu100v2`
- Service account: `fsu100v2-sa@chiops.iam.gserviceaccount.com`
- Region: `europe-west2`
- Config bucket: `gs://chiops-fsu100v2-config/`
- Events bucket (fallback for LBCF/FSU2A): `gs://chiops-fsu100v2-events/`
- Pub/Sub topic: `chimera-fsu100v2-events`
- Credentials: **none** (no Betfair, no Secret Manager)

## References

- CHI-POL-003 — Credentials in Secret Manager (FSU100V2 has none)
- CHI-POL-004 — `--no-allow-unauthenticated`
- CHI-POL-005 — FSU Build Workflow
- CHI-POL-006 — Portal as Single Auth Boundary (no env vars for settings)
- CHI-POL-008 — Shell-First Build Policy
- CHI-ADR-010 — Three Endpoint Sets
- CHI-ADR-013 — One task, one job
- CHI-ADR-014 — Portal Proxy Pattern
- CHI-ADR-018 — Live Betting Control FSU (FSU100V2 outputs TO this, never bypasses)
- Bible §20 — Event Envelope + Courier
- Bible §21 — Source Manifest
- Bible §24 — Data Capture Architecture
