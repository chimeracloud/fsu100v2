# FSU100V2 — Deploy Checklist

> Operator runs each command manually. Verify after every step. No automation.
> Stop at the first failure and resolve before continuing.
>
> **CLEv2 (`fsu100`) stays running.** This deploy stands FSU100V2 up alongside,
> in DRY_RUN, for parity testing. Cutover from CLEv2 → FSU100V2 is a separate
> change after Phase 5 parity passes.

**Project / region (locked)**: `chiops` / `europe-west2`
**Service account (locked)**: `fsu100v2-sa@chiops.iam.gserviceaccount.com`
**Cloud Run service name (locked)**: `fsu100v2`
**Default branch on GitHub at time of deploy**: `main`
**Service URL (set by operator after first deploy)**: `https://fsu100v2-991649774709.europe-west2.run.app`

---

## What FSU100V2 talks to

| Direction | Counterparty | Auth | Notes |
|---|---|---|---|
| **Reads** | `fsu1b` SSE `/stream/horse-racing` | IAM ID token (`fsu100v2-sa` invokes `fsu1b`) | live market data |
| **Reads** | `gs://chimera-portal-config/source_manifest.json` | object-level IAM | discovers FSU1B URL |
| **Reads/Writes** | `gs://chiops-fsu100v2-config/...` | object-level IAM | host config + per-plugin configs |
| **Writes** | `gs://chiops-fsu100v2-events/...` | object-level IAM | NDJSON fallback (instructions + evaluations) |
| **Writes** | `chimera-fsu100v2-events` Pub/Sub | topic-level IAM | lifecycle events |
| **Writes** | LBCF `/orders/instructions` | IAM ID token (future) | bet placement — does not exist yet |
| **Writes** | FSU2A `/events/evaluations` | IAM ID token (future) | event recorder — does not exist yet |
| **Read by** | `cst-api` proxy | IAM ID token (`cst-api` invokes `fsu100v2`) | portal traffic only — browser never hits this directly |

**There is NO Betfair code in FSU100V2.** No app keys, no Betfair secrets, no
login. All exchange interaction belongs to FSU1B (read) and the future
LBCF/FSU2A (write).

---

## PRE-DEPLOY — GCP setup

### 0a. Enable required GCP APIs on `chiops`

One-off per project. Idempotent.

```bash
gcloud services enable \
  storage.googleapis.com \
  pubsub.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  iam.googleapis.com \
  --project=chiops
```

**Verify:**
```bash
gcloud services list --enabled --project=chiops \
  --filter="config.name:(storage.googleapis.com OR pubsub.googleapis.com OR run.googleapis.com OR cloudbuild.googleapis.com)" \
  --format="value(config.name)"
```
Expected: four lines.

> Note: Secret Manager is **not** required by FSU100V2 itself (no Betfair creds).
> It stays enabled at the project level because other FSUs use it.

---

### 0b. Create the FSU100V2 service account

The SA is what Cloud Run runs the container as, and what every subsequent IAM
grant binds to.

**Verify it exists:**
```bash
gcloud iam service-accounts describe \
  fsu100v2-sa@chiops.iam.gserviceaccount.com --project=chiops \
  --format="value(email,displayName)"
```

If verify succeeds, skip to Step 1.

**If it does NOT exist, create it:**
```bash
gcloud iam service-accounts create fsu100v2-sa \
  --display-name="FSU100V2 Horse Racing Lay Engine v2" \
  --description="Runs the fsu100v2 Cloud Run service. Pure decision engine — no Betfair code." \
  --project=chiops
```

Idempotency: existing SA returns `ALREADY_EXISTS`. Safe to ignore.

---

### 1. Create the host-config bucket

```bash
gcloud storage buckets create gs://chiops-fsu100v2-config \
  --project=chiops \
  --location=europe-west2
```

Idempotency: existing bucket → HTTP 409 `You already own this bucket`. Safe to ignore.

**Verify:**
```bash
gcloud storage ls gs://chiops-fsu100v2-config/
```
Expected: no error (empty listing is fine).

---

### 2. Create the events bucket (NDJSON fallback)

```bash
gcloud storage buckets create gs://chiops-fsu100v2-events \
  --project=chiops \
  --location=europe-west2
```

> This is where FSU100V2 spools instruction + evaluation NDJSON when LBCF /
> FSU2A are unreachable (currently always, since neither service exists).
> Each day rolls into a new file:
> `gs://chiops-fsu100v2-events/instructions/{YYYY-MM-DD}.ndjson`
> `gs://chiops-fsu100v2-events/{YYYY-MM-DD}.ndjson` (evaluations)

**Verify:**
```bash
gcloud storage ls gs://chiops-fsu100v2-events/
```

---

### 3. Grant FSU100V2 SA `objectAdmin` on both own buckets

```bash
for BUCKET in chiops-fsu100v2-config chiops-fsu100v2-events; do
  gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
    --member="serviceAccount:fsu100v2-sa@chiops.iam.gserviceaccount.com" \
    --role="roles/storage.objectAdmin";
done
```

**Verify:**
```bash
for BUCKET in chiops-fsu100v2-config chiops-fsu100v2-events; do
  echo "--- $BUCKET ---"
  gcloud storage buckets get-iam-policy gs://$BUCKET \
    --format="value(bindings)" | grep fsu100v2
done
```
Expected: a line per bucket mentioning `fsu100v2-sa` and `roles/storage.objectAdmin`.

---

### 4. Grant FSU100V2 SA access to the source-manifest bucket

`gs://chimera-portal-config` is the shared discovery surface. FSU100V2 reads
the manifest to find FSU1B's URL, and writes its own entry so future consumers
(cst-api, future FSUs) can discover it.

```bash
# Read + write on chimera-portal-config (so it can update its own entry).
gcloud storage buckets add-iam-policy-binding gs://chimera-portal-config \
  --member="serviceAccount:fsu100v2-sa@chiops.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

**Verify:**
```bash
gcloud storage buckets get-iam-policy gs://chimera-portal-config \
  --format="value(bindings)" | grep fsu100v2
```

---

### 5. Create the Pub/Sub topic

```bash
gcloud pubsub topics create chimera-fsu100v2-events --project=chiops
```

Idempotency: existing topic → `ALREADY_EXISTS`. Safe to ignore.

**Verify:**
```bash
gcloud pubsub topics list --project=chiops --format="value(name)" | grep fsu100v2
```
Expected: `projects/chiops/topics/chimera-fsu100v2-events`.

---

### 6. Grant FSU100V2 SA publisher role on the topic

```bash
gcloud pubsub topics add-iam-policy-binding chimera-fsu100v2-events \
  --member="serviceAccount:fsu100v2-sa@chiops.iam.gserviceaccount.com" \
  --role="roles/pubsub.publisher" \
  --project=chiops
```

**Verify:**
```bash
gcloud pubsub topics get-iam-policy chimera-fsu100v2-events --project=chiops \
  --format="value(bindings)" | grep fsu100v2
```

---

### 7. Grant FSU100V2 SA `run.invoker` on FSU1B

The engine consumes FSU1B's authenticated SSE. The SA needs IAM at the edge of
the upstream service.

```bash
gcloud run services add-iam-policy-binding fsu1b \
  --region=europe-west2 --project=chiops \
  --member="serviceAccount:fsu100v2-sa@chiops.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

**Verify:**
```bash
gcloud run services get-iam-policy fsu1b \
  --region=europe-west2 --project=chiops \
  --format="value(bindings)" | grep fsu100v2
```

> When LBCF and FSU2A exist, repeat this step against each — `fsu100v2-sa`
> needs `run.invoker` on every downstream it POSTs to.

---

## DEPLOY

### 8. Push `main`

The repo `chimeracloud/fsu100v2` is wired to Cloud Build with a push-to-main
trigger (operator confirmed this when the service was first stood up). Every
push to `main` rebuilds + deploys a new revision automatically.

```bash
cd ~/Projects/fsu100v2
git status   # confirm working tree is clean / commits are the ones you want
git log --oneline origin/main..HEAD
git push origin main
```

**Verify the build:**
```bash
gcloud builds list --project=chiops --limit=5 \
  --format="table(id,status,createTime,substitutions.TRIGGER_NAME)"
```
Expected: most-recent row is `SUCCESS` for the fsu100v2 trigger.

If you prefer to bypass the trigger and push a manual revision (e.g. you want
to deploy a feature branch for testing without merging to main):

```bash
gcloud run deploy fsu100v2 \
  --source=. \
  --project=chiops \
  --region=europe-west2 \
  --service-account=fsu100v2-sa@chiops.iam.gserviceaccount.com \
  --no-allow-unauthenticated \
  --min-instances=1 \
  --max-instances=1 \
  --cpu=1 \
  --memory=512Mi \
  --cpu-boost \
  --no-cpu-throttling \
  --port=8080
```

> **Notes**
> - `--no-allow-unauthenticated` enforces IAM at the edge (CHI-POL-004).
> - `--min-instances=1` + `--no-cpu-throttling` keep the persistent SSE consumer
>   alive between requests — Cloud Run would otherwise idle the container and
>   drop the upstream connection.
> - `--max-instances=1` — only one process can hold the SSE supervisor +
>   evaluator pipeline; scaling would multiply instructions per event.
> - Do **NOT** set `FSU100V2_DISABLE_GCP_IO` in production env. Tests set it;
>   production does not.

**Verify the active revision:**
```bash
SERVICE_URL=$(gcloud run services describe fsu100v2 \
  --region=europe-west2 --project=chiops \
  --format='value(status.url)')
echo "SERVICE_URL=$SERVICE_URL"

gcloud run services describe fsu100v2 \
  --region=europe-west2 --project=chiops \
  --format='value(status.latestReadyRevisionName)'
```

---

### 9. Set `SERVICE_URL` env var (so the manifest advertises this URL)

Cloud Run only knows its own URL after first deploy. The source-manifest writer
needs `SERVICE_URL` to advertise FSU100V2's location for future consumers.

```bash
gcloud run services update fsu100v2 \
  --region=europe-west2 --project=chiops \
  --update-env-vars="SERVICE_URL=$SERVICE_URL"
```

A new revision rolls out automatically with the env var injected.

---

### 10. Verify the deploy (curl from your laptop)

```bash
TOKEN=$(gcloud auth print-identity-token)

curl -s -H "Authorization: Bearer $TOKEN" "$SERVICE_URL/health"   ; echo
curl -s -H "Authorization: Bearer $TOKEN" "$SERVICE_URL/ready"    ; echo
curl -s -H "Authorization: Bearer $TOKEN" "$SERVICE_URL/info"     ; echo
curl -s -H "Authorization: Bearer $TOKEN" "$SERVICE_URL/admin/status" | jq .
curl -s -H "Authorization: Bearer $TOKEN" "$SERVICE_URL/admin/config" | jq .
```

Expected:
- `/health` → `{"status":"ok"}`
- `/ready`  → `{"ready":true,...}`
- `/info`   → `{"service":"fsu100v2","phase":3,...}`
- `/admin/status` → `source.state="disconnected"`, `plugins` includes
  `mark_6_rules_v1`, `warnings=[]`
- `/admin/config` → `auto_start=false`, `dry_run=false`, `loaded_plugins=["mark_6_rules_v1"]`

If you get `401` or `403`, your account is missing `roles/run.invoker` on
`fsu100v2` or the IAM token has expired. Resolve before proceeding.

---

## POST-DEPLOY

### 11. Wire the portal proxy (cst-api repo)

cst-api lives in the `chimera-portal-api` repo. The browser never talks to
FSU100V2 directly (CHI-ADR-014).

Two edits in cst-api (these are already done — listed here so the next operator
can verify):

1. **IAM** — grant cst-api's SA `roles/run.invoker` on the FSU100V2 service:
   ```bash
   gcloud run services add-iam-policy-binding fsu100v2 \
     --region=europe-west2 --project=chiops \
     --member="serviceAccount:991649774709-compute@developer.gserviceaccount.com" \
     --role="roles/run.invoker"
   ```
2. **PROXY_TARGETS** — confirm `fsu100v2` is in the proxy target map. From the
   running cst-api revision:
   ```bash
   gcloud run services describe cst-api \
     --region=europe-west2 --project=chiops \
     --format="value(spec.template.spec.containers[0].env)" | tr ',' '\n' | grep -i proxy_targets
   ```
   Expected: a comma-separated list containing `fsu100v2=https://fsu100v2-...run.app`.

**Verify from the portal:**
- Visit `chimerasportstrading.com` → Technical → FSU Management.
- FSU100V2 chip should appear in the fleet status bar and the card grid.
- Click the chip → routes to `/technical/fsu/fsu100v2/dashboard`, which renders
  the FSU100V2Engine page.
- Status cards should match what step 10 returned from `/admin/status`.

If the page shows a 502, the cst-api proxy can't reach the service — check
step 11's IAM binding.

---

### 12. Force a Source Manifest re-register

After step 9 the URL is in settings; the manifest may have been written before
the URL was known. Force a fresh write:

```bash
TOKEN=$(gcloud auth print-identity-token)
curl -X POST "$SERVICE_URL/admin/control/reregister_source" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

Expected: `{"action":"reregister_source","accepted":true,"executed":true,...}`.

**Verify the manifest:**
```bash
gcloud storage cat gs://chimera-portal-config/source_manifest.json | jq .fsu100v2
```

Expected: an `fsu100v2` entry with `url` matching `$SERVICE_URL`, role
`engine`, and an `inputs` block referencing `fsu1b` as its source.

---

### 13. Start in DRY_RUN and watch a session

DRY_RUN evaluates every event through the plugin pipeline and writes
instructions to NDJSON, but does **not** POST anything to LBCF/FSU2A — perfect
parity-testing surface.

```bash
TOKEN=$(gcloud auth print-identity-token)
curl -X PUT "$SERVICE_URL/admin/config" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'

curl -X POST "$SERVICE_URL/admin/control/start" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

Then open the portal → Technical → FSU100V2 → Dashboard.

Expected within ~10s:
- Yellow `DRY_RUN` banner visible.
- `source.state="connected"` (green badge).
- Evaluation + skip counters tick up as races come into play.
- Instruction counter ticks up too — these are the writes that would have gone
  to LBCF if it existed.
- Live SSE feed shows `evaluation` events arriving.
- Activity feed shows `event:engine_started` then `event:market_change` lines.

```bash
# Spot-check the NDJSON spool.
TODAY=$(date -u +%Y-%m-%d)
gcloud storage cat gs://chiops-fsu100v2-events/instructions/$TODAY.ndjson | head -3 | jq .
```

---

### 14. Stop and switch back to LIVE-write **only after parity passes**

Phase 5 parity testing runs DRY_RUN FSU100V2 in parallel with CLEv2 for a
trading session and compares instruction sets. The cutover protocol lives in
`PHASE5_RESULTS.md` (to be written when parity is run); the gist:

1. CLEv2 stays the production decision engine until parity passes.
2. FSU100V2 stays in DRY_RUN until LBCF exists AND parity is signed off.
3. Operator turns DRY_RUN off **only** with explicit sign-off.

To stop the engine when DRY_RUN testing is done:
```bash
curl -X POST "$SERVICE_URL/admin/control/stop" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

---

## Rollback

If anything goes wrong during deploy:

```bash
# List revisions:
gcloud run revisions list --service=fsu100v2 \
  --region=europe-west2 --project=chiops

# Roll back to a known-good revision:
gcloud run services update-traffic fsu100v2 \
  --region=europe-west2 --project=chiops \
  --to-revisions=fsu100v2-<previous-revision>=100
```

If a deploy is actively dangerous (e.g. it would POST live bets):

```bash
# Belt-and-braces: stop the engine, then flip DRY_RUN on.
TOKEN=$(gcloud auth print-identity-token)
curl -X POST "$SERVICE_URL/admin/control/stop" -H "Authorization: Bearer $TOKEN"
curl -X PUT  "$SERVICE_URL/admin/config" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"dry_run": true}'
```

For a complete teardown:

```bash
gcloud run services delete fsu100v2 \
  --region=europe-west2 --project=chiops --quiet
```

The CLEv2 service (`fsu100`) is untouched by any of this — it stays running on
its own revision, its own SA, its own buckets. FSU100V2 going down does not
affect live betting on CLEv2.

---

## Idempotency notes

- Steps 0a, 0b, 1, 2, 5: idempotent (errors on existing resource are safe).
- Steps 3, 4, 6, 7, 11: idempotent (re-binding the same role is a no-op).
- Step 8: deploys a new revision; old revision stays for rollback.
- Step 9: new revision again; previous one rollback-able.
- Steps 10, 12, 13: read-only / state-changing-but-recoverable.

---

## Operator checklist (tick as you go)

- [ ] 0a. Required GCP APIs enabled on `chiops`
- [ ] 0b. Service account `fsu100v2-sa@chiops` exists
- [ ] 1. `gs://chiops-fsu100v2-config` exists
- [ ] 2. `gs://chiops-fsu100v2-events` exists
- [ ] 3. FSU100V2 SA has `objectAdmin` on both own buckets
- [ ] 4. FSU100V2 SA has `objectAdmin` on `chimera-portal-config`
- [ ] 5. `chimera-fsu100v2-events` Pub/Sub topic exists
- [ ] 6. FSU100V2 SA has `pubsub.publisher` on the topic
- [ ] 7. FSU100V2 SA has `run.invoker` on `fsu1b`
- [ ] 8. `git push origin main` triggered a successful Cloud Build deploy; `$SERVICE_URL` captured
- [ ] 9. `SERVICE_URL` env var set on the service
- [ ] 10. `/health`, `/ready`, `/info`, `/admin/status`, `/admin/config` all 200
- [ ] 11. cst-api proxy reaches FSU100V2; FSU Management page renders the chip + card
- [ ] 12. Source Manifest contains `fsu100v2` entry with correct URL
- [ ] 13. DRY_RUN start: dashboard shows connected source + evaluations ticking; NDJSON in spool

Once all 13 are ticked, FSU100V2 is operationally ready for Phase 5 parity
testing against CLEv2. Do **not** disable DRY_RUN until parity is signed off
AND LBCF exists.

---

*Born from complexity. Engineered for certainty.*
