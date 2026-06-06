# Creative Review Analysis Service

AI-powered creative review service that accepts creative submissions - campaign briefs, taglines, ad concepts, social posts, scripts, treatments, and visual assets - and returns structured analyses to help creative teams triage and review work.

**Live demo:** [FILL IN RAILWAY URL]
**Walkthrough video:** [FILL IN VIDEO URL]
**Repository:** [FILL IN GITHUB URL]

---

## Quick demo

Submit a creative for analysis against the live deployment:

```bash
curl -X POST [FILL IN RAILWAY URL]/submissions \
  -H "X-API-Key: [FILL IN]" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Spring Activewear Launch",
    "content_type": "campaign_brief",
    "brief_text": "Launching our new sustainable activewear line for Gen Z women. The campaign should feel energetic but authentic. Hero film opens on friends meeting at sunrise for a community run. Tagline candidate: Move Like You Mean It.",
    "target_audience": "Women 18-25, urban, climate-aware",
    "campaign_goal": "Drive 15% increase in Q2 trial purchases"
  }'
```

The submission auto-routes to the **rich tier** (because of `content_type: campaign_brief`), runs a two-stage extract→evaluate analysis asynchronously, and returns a submission ID. Poll `GET /submissions/{id}` for results, or open `[FILL IN RAILWAY URL]/dashboard` to watch it complete in the UI.

---

## What it does

The service answers a single question for creative teams: *"What should we do next with this piece of work?"*

For each submission, it returns:

- **Scores** across six dimensions (concept, execution, audience fit, brand alignment, originality, impact) with rationales
- **Strengths** - what's working, anchored to specific content
- **Improvements** - specific suggestions
- **Target audience interpretation** - who the work seems aimed at
- **Tone** - how the work reads
- **Recommendations** - ranked next actions
- **Confidence score** - how certain the model is in its evaluation

Submissions can include text content plus optional image URLs. The service analyzes both.

---

## Architecture overview

```
Client (UI or API)
        │
        ▼
   FastAPI app
        │
        ├──▶ Router  ──── decides tier (fast / rich) + reason
        │
        ├──▶ AnalysisStrategy
        │       │
        │       ├──▶ ModelPool (fast)  ──┐
        │       │                         │
        │       └──▶ ModelPool (rich)  ───┼──▶ Adapters (NIM, Anthropic, Mock)
        │                                 │
        │                                 └──▶ retries + provider failover
        │
        └──▶ Repository ──▶ Postgres / SQLite
                                │
                                └──▶ append-only `analyses` table
                                     with processing_metadata (JSONB)
```

The system is built around **three layers of fallback**, each with one concern:

1. **Retry layer** - `tenacity` retries transient errors (timeout, 5xx, rate limits). Does NOT retry auth, bad request, or schema validation errors.
2. **Pool layer** - A `ModelPool` is an ordered list of `(provider, model)` pairs. Tries each in order; returns the first success or raises `PoolExhaustedError`.
3. **Strategy layer** - Decides what to do when a pool is exhausted. Rich tier falls back to fast tier; fast tier falls back to mock. Both paths mark results as `degraded` with an explicit reason.

This separation means each layer is independently testable and the fallback chain is data-driven (configured via env vars), not hard-coded.

---

## Tier routing

A `TierRouter` evaluates submissions against ordered rules and returns `(tier, reason)`:

| Priority | Rule | Routes to |
|---|---|---|
| 1 | `tier_override == "rich"` (explicit user request) | rich |
| 2 | `tier_override == "fast"` | fast |
| 3 | Any `asset_urls` present (images) | rich |
| 4 | `content_type` in `{creative_brief, campaign_brief, script_excerpt, treatment, pitch}` | rich |
| 5 | `brief_text` length > 2000 chars | rich |
| 6 | At least 2 rich-context keyword matches | rich |
| 7 | Default | fast |

**Rich-context keywords** include strategic vocabulary like *positioning, brand voice, audience, key message, creative direction, big idea, story arc, treatment, moodboard*. The 2-keyword threshold prevents false positives from single-word matches.

**Every routing decision logs a human-readable reason** (e.g., `content_type:campaign_brief`, `keyword_match:positioning, tone of voice (+3 more)`, `long_text:2347`) that's stored in `processing_metadata.routing_reason` for audit and tuning.

### Why a "Deep analysis" toggle isn't a gate

The UI's "Force deep analysis" toggle is an **upgrade-only override** - it forces rich tier when the router would have picked fast. Critically, **the router still runs by default**: a long script excerpt with images will route to rich tier whether the user toggles the option or not. The toggle raises the ceiling; the router protects the floor.

---

## Reliability & fallback

The rich tier runs a two-stage analysis: **extract** (gather structured facts from the submission) → **evaluate** (produce judgments grounded in the extraction). Each stage uses provider failover within the rich pool.

### Fast tier fallback chain

```
Fast pool, model 1  →  retries  →  fail
Fast pool, model 2  →  retries  →  fail
Mock provider (marked degraded)
```

Fast tier is synchronous - the user is waiting - so the chain is short.

### Rich tier fallback chain

```
Stage 1 (extract):
   Rich pool, model 1  →  retries  →  fail
   Rich pool, model 2  →  retries  →  fail
   ↓ if exhausted
Stage 2 (evaluate):
   Rich pool, model 1  →  retries  →  fail
   Rich pool, model 2  →  retries  →  fail
   ↓ if either stage exhausted
Fast-pool single-stage fallback (marked degraded)
   ↓ if also exhausted
Mock provider (marked degraded)
```

Rich tier runs asynchronously, so the chain can be longer.

### Failure type taxonomy

The retry layer distinguishes retryable from non-retryable errors:

- **Retryable** (retry with backoff): timeouts, 5xx, 429 rate limits, connection errors
- **Non-retryable** (fail fast, propagate to pool): auth errors, bad request, content policy refusals, schema validation failures

This prevents wasted retries on permanent errors and gets to the fallback faster when a real problem exists.

### Cross-provider failover, not cross-model

Within a pool, failover **switches providers** (e.g., NIM → Anthropic), not just models on the same provider. This is the correct design: provider availability failures (timeout, 5xx, rate limit) affect all models on that provider, so falling back to a different model on the same provider doesn't help. Same-provider model fallback would only help for quality/prompt failures, which are handled separately via corrective-prompt retries.

---

## Observability

### Structured logging

All logs are structured JSON, emitted to stdout. Every request is assigned a correlation ID that flows through:
- HTTP request → background task → strategy layer → pool layer → adapter call

Sample log line:

```json
{
  "event": "pool_member_success",
  "level": "info",
  "timestamp": "2026-06-06T15:40:36Z",
  "correlation_id": "c64fa163-a117-4043-8c4d-6dc9e81f410c",
  "pool": "rich",
  "model": "nim:nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
  "stage": "stage_1",
  "latency_ms": 10056,
  "retries": 0
}
```

### Correlation ID propagation

Every HTTP response includes an `X-Correlation-ID` header. Clients can supply their own ID via the same header to trace cross-system requests.

### Where to find logs

- **Locally:** `make dev` streams logs to the terminal, or `docker compose logs -f` if running via Docker
- **Deployed (Railway):** logs are captured by Railway's built-in log viewer (dashboard → service → "Logs" tab), searchable by correlation ID

Logs are intentionally **not** stored in the application database - they're transient operational events with different access patterns and retention needs than business data.

### Per-submission audit trail

While logs are transient, each analysis records its full processing history permanently in `analyses.processing_metadata` (JSONB):

```bash
curl [FILL IN RAILWAY URL]/submissions/{id}/audit
```

Returns:

```json
{
  "routing_reason": "content_type:campaign_brief",
  "requested_tier": "rich",
  "served_tier": "rich",
  "stages_completed": ["stage_1", "stage_2"],
  "pool_attempts": [
    {"pool": "rich", "model": "nim:nvidia/...", "stage": "stage_1", "outcome": "success", "latency_ms": 10056, "retries": 0},
    {"pool": "rich", "model": "nim:nvidia/...", "stage": "stage_2", "outcome": "success", "latency_ms": 9155, "retries": 0}
  ],
  "total_latency_ms": 19215,
  "degraded": false,
  "degradation_reason": null
}
```

### Aggregate metrics

```bash
curl [FILL IN RAILWAY URL]/metrics
```

Returns counts by tier, by status, degradation rate and reasons, latency percentiles per tier, and per-provider call/error rates.

### Storage separation

- **Status fields** (`tier_used`, `degraded`, `degradation_reason`, `total_latency_ms`, `prompt_version`) are typed columns on the `analyses` table - queryable and indexable.
- **Audit log** (`pool_attempts`, `token_usage`, `stages_completed`) lives in the JSONB `processing_metadata` column - flexible shape, read whole when investigating a specific row.
- **API responses are projections** — list endpoints return summary status only, the detail endpoint returns result + status, the `/audit` endpoint returns the full processing trail on demand.

This keeps the API lean while preserving full debuggability.

---

## API reference

Authentication uses the `X-API-Key` header on protected endpoints. Health, metrics, and dashboard routes are open.

### Submissions

#### Create

```http
POST /submissions
X-API-Key: <key>
Content-Type: application/json

{
  "title": "Campaign Concept",
  "brief_text": "...",
  "content_type": "campaign_brief",
  "asset_urls": [],
  "tier_override": "auto"
}
```

**Content types** (used for routing and prompt context):
`auto`, `tagline`, `headline`, `social_post`, `ad_copy`, `creative_brief`, `concept`, `campaign_brief`, `script_excerpt`, `treatment`, `pitch`

**Tier override:** `auto` (default — let the router decide), `fast`, `rich`

Returns the submission with status `pending` (rich tier) or `complete` (fast tier).

#### List

```http
GET /submissions?limit=20&offset=0
```

#### Detail

```http
GET /submissions/{submission_id}
```

#### Audit metadata

```http
GET /submissions/{submission_id}/audit
```

### Health & metrics

```http
GET /health
GET /metrics
```

---

## Dashboard

Open `[FILL IN RAILWAY URL]/dashboard` to use the browser UI:

- Create submissions via form
- View the review queue with status, tier, and degraded badges
- Filter by status and tier; sort by newest, needs attention, or longest waiting
- View live analysis status (HTMX polling)
- Reanalyze a submission, optionally forcing rich tier
- Inspect the full audit trail via "Show processing details"

---

## Evals

The eval suite verifies routing logic, response shape, and provider behavior using 15 fixtures covering:

- Fast/rich explicit overrides
- Auto-routing for short, simple content (expects fast)
- Keyword-density escalation (expects rich)
- Long-brief routing (expects rich)
- Asset-bearing submissions (expects rich)
- Rich content type routing
- Rebrand routing regression
- Required output fields and score dimensions/ranges

```bash
# Mock provider (fast, deterministic, no API quota used)
make evals

# Against real configured pools
EVAL_PROVIDER=strategy make evals

# Persist result snapshots
EVAL_RESULTS_DIR=evals/results make evals

# Fetch real image URLs during evals
EVAL_FETCH_ASSETS=1 EVAL_PROVIDER=strategy make evals
```

Fixtures are JSON files in `evals/fixtures/`. Adding a new test case is dropping a JSON file - no code change required.

---

## Setup

### Local development

```bash
make install
cp .env.example .env
# (optional) edit .env to add API keys for real providers; defaults to mock
make dev
```

Open `http://localhost:8000/dashboard`.

The default configuration uses SQLite (`reviews.db` in the project root) and the mock provider — no external dependencies, no API keys required. To use real providers, set `NIM_API_KEY` and/or `ANTHROPIC_API_KEY` in `.env`.

### Docker

```bash
make docker-up        # run the API
make docker-test      # run unit/integration tests
make docker-evals     # run eval suite
```

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `API_KEYS` | `devkey1` | Comma-separated API keys |
| `DATABASE_URL` | `sqlite+aiosqlite:///./reviews.db` | DB connection string |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `RATE_LIMIT_PER_MINUTE` | `60` | Per-key rate limit |
| `IMAGE_MAX_BYTES` | `5242880` | Max fetched image size (5 MB) |
| `NIM_API_KEY` | (empty) | NVIDIA NIM key |
| `NIM_BASE_URL` | `https://integrate.api.nvidia.com/v1` | NIM endpoint |
| `ANTHROPIC_API_KEY` | (empty) | Anthropic key |
| `FAST_POOL` | `nim:meta/llama-3.3-70b-instruct,anthropic:claude-haiku-4-5` | Fast tier pool |
| `RICH_POOL` | `nim:nvidia/nemotron-3-nano-omni-30b-a3b-reasoning,anthropic:claude-opus-4-8` | Rich tier pool |
| `RETRY_MAX_ATTEMPTS` | `2` | Retries per provider call |
| `RETRY_INITIAL_WAIT_S` | `1.0` | Initial backoff |
| `RETRY_MAX_WAIT_S` | `10.0` | Max backoff |
| `PROVIDER_TIMEOUT_S` | `30.0` | Per-call timeout |

Railway provides `DATABASE_URL` automatically when a Postgres service is attached; the app normalizes `postgres://` and `postgresql://` to `postgresql+asyncpg://` at startup.

---

## Railway deployment

1. Push to GitHub
2. Create a Railway project from the repo
3. Add a Railway Postgres service
4. Configure app environment variables:

```env
API_KEYS=<long-random-key>
DATABASE_URL=${{Postgres.DATABASE_URL}}
NIM_API_KEY=<your-nim-key>
ANTHROPIC_API_KEY=<your-anthropic-key>
FAST_POOL=nim:meta/llama-3.3-70b-instruct,anthropic:claude-haiku-4-5
RICH_POOL=nim:nvidia/nemotron-3-nano-omni-30b-a3b-reasoning,anthropic:claude-opus-4-8
```

The Dockerfile uses Railway's dynamic `PORT`. After deployment, hit `/health` and `/dashboard` to verify.

---

## Key tradeoffs

**Pool-based routing with provider-prefixed model IDs.** Configuration is a single comma-separated env var per pool. Adding a model is a one-line change. Adding a provider requires one new adapter module. This is the seam that makes evolution cheap.

**Append-only analyses with JSONB processing_metadata.** Every reanalysis creates a new row. Same submission can be analyzed multiple times against different prompts or configurations, and the full history is preserved for audit and regression checking. The JSONB column means the audit shape can evolve without migrations.

**Synchronous fast tier, asynchronous rich tier.** Fast tier completes in 1–3 seconds and is delivered in the response. Rich tier returns a submission ID immediately and runs in a `BackgroundTasks`. The dashboard polls for status updates. Background tasks take only the submission ID and refetch from the DB, so migrating to a real queue (Arq, Celery) is mechanical — no business logic changes needed.

**HTMX polling for async updates.** Polling has a 2s starting interval with backoff to 10s, paused when the tab is hidden. For production scale, Server-Sent Events would eliminate the per-poll DB query; for prototype scale, polling is the simplest pattern that works.

**`create_all()` on startup for schema management.** Acceptable for demos and fresh databases. A production deployment would use Alembic migrations. Documented explicitly so it's not mistaken for production-ready.

**SQLite locally, Postgres on Railway.** Same SQLAlchemy code path for both. Local dev needs zero external dependencies. The URL normalization step handles Railway's connection string conventions.

---

## What was intentionally not built

- **Multi-tenancy** (workspaces, users, roles) - out of scope for a single-team internal tool
- **Real distributed job queue** (Celery, Arq) - BackgroundTasks suffices at this scale; migration path documented above
- **Webhooks / external notifications** - additive; can slot in at the end of `run_analysis`
- **Server-Sent Events for status polling** - polling is sufficient for prototype scale
- **Agentic / multi-step analysis with tool use** - single-pass or two-stage only; agentic flows would be a larger architectural change
- **Distributed tracing** (OpenTelemetry) - correlation IDs are the prototype equivalent; OTel migration is mostly instrumentation
- **PII detection and redaction** before sending to LLMs - production hardening, scoped out
- **Cost enforcement / budget caps** - token usage is recorded per analysis but not gated
- **Per-content-type prompt variants** - single prompt per tier; per-type variants are a clear future improvement
- **LLM-as-judge in evals** - current semantic checks are keyword-based, which is brittle but cheap and deterministic
- **JSON API endpoint for reanalyze** - reanalyze exists as a dashboard route (`POST /dashboard/{id}/reanalyze`) but not yet on `/submissions`. Easy addition if needed
- **CSRF protection on dashboard forms** - required before public deployment
- **SSRF hardening on image URL fetching** - required before accepting URLs from untrusted users; currently size-limited only

---

## Where AI tools helped, and what I verified or changed

I used Claude (via the chat interface) extensively for design discussions and Claude Code for implementation. The breakdown:

**Claude (design):**
- Worked through the routing architecture, fallback chain design, and the tier/stage/model separation as conversation. The "three independent decisions bundled by tier" mental model came out of this discussion.
- Reviewed early designs and pushed back on weak ones (e.g., I caught a flaw in the same-provider-model fallback design through this back-and-forth, which led to the current cross-provider-only architecture).
- Helped articulate tradeoffs and the future-work prioritization.

**Claude Code (implementation):**
- Scaffolded the FastAPI app structure, Pydantic schemas, SQLAlchemy models, and Docker setup.
- Implemented the pool/strategy/adapter classes against a clear specification.
- Built the HTMX dashboard templates and polling logic.
- Generated initial test fixtures and the pytest runner.

**What I verified or modified manually:**
- The routing rules and keyword set — iterated based on test inputs to reduce false positives.
- The decision to **not** retry across same-provider models for availability failures — Claude Code initially generated a chain that did this; I refactored it because the rationale is fundamentally different (model fallback helps for quality, provider fallback helps for availability).
- The audit log honesty issue — discovered through real log inspection that `pool_member_success` was firing on HTTP success even when validation later failed. Fixed to record outcomes at the validation layer.
- The content_type routing wiring — UI was sending tab selections that the router was ignoring; threaded the field through schema → DB → router.
- Prompt design for the extract and evaluate stages — manually tuned for output structure and groundedness.

The pattern was: use AI tools for surface-area-heavy work (scaffolding, boilerplate, repetitive code) and manual review/redesign for decisions that have architectural consequences. Every meaningful design decision in the README was made consciously, not accepted from generated code.

---

## Future work

If I had more time, in priority order:

1. **LLM-as-judge eval layer** — current semantic checks are keyword-based and brittle. A judge model scoring analyses against rubrics would catch quality regressions that keyword matching misses.

2. **Per-content-type prompt variants** — a script excerpt benefits from different evaluation than a tagline. The prompt system is already file-based; adding type-specific variants is additive.

3. **Server-Sent Events for status updates** — replace polling with a single push-based connection. Eliminates per-poll DB load and improves perceived latency.

4. **Real distributed job queue** — migrate from `BackgroundTasks` to Arq or Celery for persistence across restarts and horizontal scaling. Background tasks already take IDs and refetch, so the migration is mechanical.

5. **A/B testing framework for prompts** — analyses are already tagged with `prompt_version`. Adding traffic-splitting in the prompt loader enables structured prompt experimentation.

6. **Per-key budget caps and rate tiers** — token usage is recorded; adding budget enforcement is a query + a check in the router.

7. **SSRF hardening + CSRF protection** — required before exposing image URL fetching or the dashboard to untrusted users.

8. **OpenTelemetry tracing** — correlation IDs become trace IDs, sent to a service like Honeycomb or Grafana Tempo. Mostly instrumentation, not refactor.
