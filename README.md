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
- **API responses are projections** - list endpoints return summary status only, the detail endpoint returns result + status, the `/audit` endpoint returns the full processing trail on demand.

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

**Tier override:** `auto` (default - let the router decide), `fast`, `rich`

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

The default configuration uses SQLite (`reviews.db` in the project root) and the mock provider - no external dependencies, no API keys required. To use real providers, set `NIM_API_KEY` and/or `ANTHROPIC_API_KEY` in `.env`.

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


## Key tradeoffs

### Pool-based provider abstraction

A model pool is an ordered list of `(provider, model)` pairs configured via a single env var per tier. Adding a model is a one-line change; adding a provider requires one new adapter module. The alternative - branching on provider type inside the strategy layer - would have scattered provider-specific code throughout the codebase. The cost is one extra layer of indirection, which pays for itself the first time you add a fallback provider.

### Cross-provider failover, not cross-model failover

When a provider call fails (timeout, 5xx, rate limit), the pool moves to the next *provider*, not the next model on the same provider. Same-provider model fallback only helps for quality or prompt failures - for availability failures, the provider infrastructure is shared, so the second model fails the same way. Quality failures are handled separately via corrective-prompt retries on the same provider. This is the design most prototypes get subtly wrong.

### Append-only analyses with JSONB processing metadata

Every reanalysis creates a new row. This gives audit trail, prompt-version diffing, A/B test capability, and reanalysis history essentially for free. Update-in-place would have required a real migration the first time we wanted any of those. Status fields (`tier_used`, `degraded`, `total_latency_ms`) are typed columns for fast filtering; the audit log (`pool_attempts`, `token_usage`) lives in a JSONB column whose shape can evolve without migrations.

### Synchronous fast tier, asynchronous rich tier

The tier decision bundles three concerns: model capability, prompt strategy (single-stage vs two-stage), and execution mode (sync vs async). They're correlated in practice and bundling them avoids exposing three independent toggles to the user. Background tasks for the rich tier take only the submission ID and refetch from the database - making them safe under restarts and migration-ready to a real queue (Arq, Celery) without business logic changes.

### Rule-based routing instead of ML-based

The router uses a priority cascade of explicit rules. Rules are deterministic, testable, debuggable, and explainable; every routing decision logs a human-readable reason. An LLM-based classifier would add a model call before the model call, require labeled training data we don't have, and make routing decisions opaque. Once production data exists, an ML router could swap in behind the same interface.

### HTMX polling for async result delivery

The dashboard polls `/dashboard/{id}/status` with a 2s starting interval, backing off to 10s, paused when the tab is hidden. Server-Sent Events would eliminate per-poll database queries and improve perceived latency, but require a long-lived connection and additional client complexity. At prototype scale, polling overhead is negligible. SSE is the production answer; polling is documented as a deliberate scope choice.

### MockProvider as a first-class adapter

The mock provider implements the same `AnalysisAdapter` interface as NIM and Anthropic. This is what lets the system run end-to-end with zero API keys, what lets the eval suite execute deterministically in CI, and what serves as the final fallback when every real provider fails. Treating mock as a real adapter rather than a special case means it stays in sync with the response schema on every test run.

### Single connection-string DB layer over engine-specific abstractions

The same SQLAlchemy code runs against any DB engine supported by SQLAlchemy, switched via `DATABASE_URL`. Railway provides Postgres in production; SQLite works for local dev with zero setup. The alternative - writing engine-specific code paths - would have doubled the surface area for marginal benefit. The cost is occasionally avoiding Postgres-specific syntax in raw queries; the benefit is a frictionless local-to-deployed migration path and trivial engine swaps.

### Structured logging with correlation IDs over heavy tracing infrastructure

Every request gets a correlation ID that flows through HTTP → background task → strategy → pool → adapter, logged as structured JSON to stdout. The platform's log viewer (Railway's, Render's, etc.) provides the search and filter UI. The alternative - wiring OpenTelemetry to Honeycomb or Datadog from day one - would have added vendor dependency, configuration overhead, and a paid service tier. Correlation IDs are the prototype-grade equivalent and they become trace IDs trivially when OTel is added later.

---

## What was intentionally not built

### Out of scope for an internal team tool

- **Multi-tenancy (workspaces, users, roles)** - no user model, no per-user data isolation. A real product serving multiple teams needs workspace-scoped submissions and RBAC. The repository pattern makes this additive - a nullable `workspace_id` plus a tenant resolver dependency covers most of it.

- **Full authentication and identity (OAuth, SSO, sessions)** - single shared API key via header is sufficient for an internal tool with known users. Real auth needs user accounts, password reset, possibly SSO.

- **Real-time collaboration** - no simultaneous editors, no comments, no approval flows. Turning the tool into a workflow product (comments, approval gates, status transitions) is a separate product.

### Different architecture, not an addition

- **Agentic or multi-step analysis with tool use** - the two-stage rich tier is the limit of orchestration. True agentic flows (search past work, look up competitors, pull from connected sources) need a tool registry and state machine. The provider abstraction would be wrapped, not replaced.

- **Streaming responses (SSE or WebSockets)** - rich tier runs to completion before storing. Streaming requires partial-result writes, a streaming endpoint, and a `stream_analyze()` provider method. Significant change for production UX value.

- **True async job queue (Celery, Arq, SQS)** - `BackgroundTasks` runs in-process: jobs don't survive restarts, can't be processed by separate workers. Migration is mechanical because tasks already take IDs and refetch, but it requires running Redis/RabbitMQ and worker processes.

### Production hardening - required before exposing to untrusted users

- **SSRF hardening on image URL fetching** - currently size-limited but doesn't block private IPs, localhost, or cloud metadata endpoints. Critical vulnerability for a public service.

- **CSRF protection on dashboard form routes** - POST submissions accepted without CSRF tokens. Mandatory for any deployment serving authenticated browser sessions.

- **PII detection and redaction before sending to LLM providers** - briefs may contain customer names, unannounced launches, competitive intelligence. Production needs a pre-processing pipeline that detects and redacts or warns. Provider zero-retention agreements would also be part of the answer.

- **Distributed rate limiting** - `slowapi` uses per-key in-memory counters that don't coordinate across instances. Horizontal scaling needs Redis-backed rate limiting.

- **Cost enforcement and budget caps** - token usage is recorded per analysis but not gated. A bulk attacker could rack up real costs. Production needs per-key budget caps, automatic tier downgrade as budgets exhaust, and spend alerting.

- **Data retention and right-to-delete** - submissions and analyses live indefinitely. GDPR and similar regulations require explicit retention policies and a deletion mechanism. Append-only analyses make deletion more involved (cascade or anonymize), but it's a known pattern.


### Deferred because low ROI now, clearly additive later

- **Webhooks and external integrations** - no Slack/Linear/Asana notifications. Architecture supports them trivially via a fire-and-forget step at the end of `run_analysis`, but no team currently consumes the events.

- **Per-content-type prompt variants** - single generic rich-tier prompt. Script excerpts benefit from different evaluation than campaign briefs. Prompt system is already file-based, so type-specific variants are additive and would noticeably improve quality.

- **LLM-as-judge in the eval suite** - current semantic checks are keyword-based: brittle but cheap and deterministic. A judge model scoring against rubrics would catch quality regressions keyword matching misses, at the cost of another model call and another source of nondeterminism.

- **Distributed tracing (OpenTelemetry)** - correlation IDs in structured logs are the prototype-grade equivalent. Migration is mostly instrumentation - correlation IDs become trace IDs, log calls become span events.


---

## Future work

### Model quality and analysis depth

- **LLM-as-judge eval layer** - highest-leverage improvement for output quality over time. Judge model scores analyses against rubrics (groundedness, specificity, actionability) on every prompt change in CI; regressions block deploys. Without this, prompt iteration is blind.

- **Per-content-type prompt variants** - type-specific prompts emphasizing relevant evaluation dimensions (pacing/character for scripts, audience-goal alignment for campaigns, memorability for taglines). Prompt loader is already file-based; this is additive.

- **Two-stage tuning and sub-task parallelism** - Stage 1 (extract) is observational and could run on a cheaper model; Stage 2 (evaluate) needs the strong model. Independent Stage 2 sub-tasks can run in parallel via `asyncio.gather`, recovering most of the latency cost.

- **RAG over past submissions and brand guidelines** - when a team uses the tool repeatedly, analyses should learn the brand voice. RAG against past analyses and brand documents enables real "does this fit our brand?" evaluation. Per-workspace isolation required.

- **Confidence intervals and explicit uncertainty** - each output field carries a confidence score, surfaced in the UI. Low-confidence claims get visual differentiation. Implemented via self-consistency or the judge layer.

### Reliability and observability at scale

- **True async job queue (Arq, Celery, SQS)** - persistent queue surviving restarts, multiple worker processes scaling independently, per-job retry policies with dead-letter handling. Migration is mechanical.

- **Circuit breaker on provider calls** - after N failures in M seconds, skip the provider for a cooldown period. Avoids the latency cost of attempting calls during outages. Hooks into the existing pool layer.

- **OpenTelemetry distributed tracing** - correlation IDs become trace IDs with span-level latency breakdowns. Cross-service propagation when more services are added. Sent to Honeycomb, Datadog, or Grafana Tempo.

- **Server-Sent Events for status updates** - replace HTMX polling with push-based connections. Eliminates per-poll DB queries; users see results the moment they're ready.

- **Formal SLOs and alerting** - defined p95 latency targets per tier, error rate thresholds, provider availability minimums. Violations surface to PagerDuty or Slack. Status page built from the metrics endpoint.

- **Automatic recovery sweep for stuck jobs** - on startup, scan for submissions in `processing` status older than a threshold and re-queue them.

### Scale and performance

- **Connection pool tuning and read replicas** - tune pool sizing based on observed concurrency. Send reads to a replica; keep the primary for writes.

- **Response caching for identical submissions** - hash submission content, cache analyses by hash. Useful for replay scenarios. Slot into the repository layer behind the same interface.

- **Provider-side prompt caching** - both Anthropic and NIM support prompt caching natively. Long system prompts can be cached, reducing per-call cost when the same prompt is used repeatedly.

- **Batch analysis endpoint** - for bulk evaluation (re-analyzing a backlog against a new prompt), an endpoint that accepts arrays and queues each. Required for eval scale and for "re-evaluate everything" workflows.

- **Multi-region deployment** - app instances in multiple regions, geographic routing, replicated Postgres. Provider endpoints are global so they need no changes.

### Multimodal expansion

- **Video support** - frame sampling + vision analysis per frame, or audio extraction → transcript → text analysis. Product question of "what does video analysis mean" needs answering first.

- **Audio support** - Whisper-based transcription then standard text analysis, plus tone/sentiment on the audio itself. Real for podcast scripts, voiceover, radio ads.

- **Asset caching in object storage** - fetched images persisted to S3, keyed by URL hash with expiry policies. Saves bandwidth and supports replay.

- **Authenticated asset URLs** - currently only public URLs work. Production needs signed S3, OAuth-fetched assets, and credentialed bucket access.

- **Pre-LLM safety filtering on images** - cheap classifier (Rekognition, SafeSearch) before paying for full vision analysis. Catches NSFW or otherwise problematic content cheaply.

### Product features

- **Webhooks and external notifications** - Slack/Linear/Asana on analysis complete. Per-workspace webhook configuration. Retry on delivery failure.

- **Comments and approval flows** - make analyses actionable: reviewers comment, request changes, mark approved/rejected, route to stakeholders. Transforms the tool from "analysis" to "workflow."

- **Submission versioning and side-by-side comparison** - track revisions of the same concept. Show v1 and v2 analyses side by side. Highlight improvements and regressions.

- **Brand guidelines integration** - uploaded brand documents (voice guides, visual standards). Analyses include explicit checks against brand rules. RAG layer over uploaded docs plus brand-aware prompts.

- **Export to PDF, Notion, Google Docs** - polished output formats for sharing analyses outside the tool. Goes with comments/approvals - analyses become workflow artifacts.

- **Multi-tenant workspaces** - per-workspace submissions, members, roles, settings, prompts. Foundational for a real SaaS product.

### Cost control and operations

- **Per-key or per-workspace budget caps with alerts** - quotas, soft warnings, hard cutoffs. Automatic tier downgrade as budget nears exhaustion. Spend dashboards per workspace.

- **Provider arbitrage based on cost and quality** - route to the cheapest provider meeting a quality bar, measured by the judge layer. Provider mix shifts as pricing evolves.

- **Tenant-level data residency** - regional Postgres deployments and region-restricted provider routing for enterprise customers requiring specific regions.

- **SOC 2 and security compliance** - audit logging, encryption at rest, penetration testing. Required for enterprise sales.

### Security and compliance

- **SSRF and CSRF hardening** - required before public deployment. URL allowlist enforcement, private-IP blocking, redirect inspection; token-based form protection on state-changing routes.

- **PII detection and redaction** - pre-LLM filtering for customer names, emails, internal product codes, unannounced launches. Either redact and proceed, or warn and require confirmation.

- **Zero-retention agreements with LLM providers** - both Anthropic and OpenAI offer agreements exempting your data from training and reducing retention windows. Required for enterprise.

- **Audit logs and right-to-delete** - track who accessed what when. Implement cascade deletion or anonymization for user data deletion requests. Standard GDPR/CCPA.



## Where AI tools helped, and what I verified or changed

I designed the architecture myself and used Claude as a thinking partner throughout. The decisions about what to build, what to leave out, and which tradeoffs to make were mine. Claude helped me pressure-test them.

**What I did myself:**

- Designed the overall architecture, including the three-layer fallback structure (retry, pool, strategy) and the separation between routing, strategy, and provider concerns
- Wrote the database models, schemas, and repository layer
- Made the product decisions: what content types to support, how the "Deep analysis" toggle should behave, how the dashboard should present degraded results, what to include in the audit metadata vs. the API response
- Chose the tradeoffs: synchronous fast vs. asynchronous rich tier, cross-provider failover instead of cross-model, append-only analyses with JSONB metadata, polling over SSE for this scope
- Wrote the routing logic from scratch, including the priority cascade, the keyword set, and the threshold tuning
- Wrote the eval fixtures and the runner
- Wrote the structured logging setup, the correlation ID middleware, and the events that get logged at each layer
- Wrote the prompts for the fast tier, extract stage, and evaluate stage

**Where Claude chat helped:**

Used it for back-and-forth design discussions: talking through tradeoffs before committing to them, stress-testing decisions, and catching weak designs early. A concrete example: I initially had a fallback chain that retried across models on the same provider for availability failures. Working through *why* that pattern would help vs. *when* it actually applies clarified that same-provider model fallback only helps for quality failures, not availability failures. That conversation directly shaped the cross-provider-only design that's in the code now. Similar conversations shaped the routing rules, the storage split between typed columns and JSONB, and the decision to make the UI toggle an upgrade-only override rather than a gate.

**Where Claude Code helped:**

Used it for surface-area work: the HTMX dashboard templates and polling logic, the test scaffolding and pytest setup, repetitive boilerplate (provider adapter shells, schema definitions), and Docker/Makefile setup. Also used it for debugging. When a bug appeared in the logs (like the `pool_member_success` log firing on HTTP success even when validation failed downstream), I'd describe the symptom and have Claude Code propose the fix, then review and adjust before applying.

**How I verified the AI-generated code:**

Every file Claude Code produced, I went through line by line. Where the generated code matched my design, I kept it. Where it didn't, and this happened often enough to matter, I rewrote it. The fallback chain was rewritten after Claude Code initially generated a version that mixed availability and quality concerns. The audit log recording was rewritten when I found it was logging "success" at the HTTP layer instead of the validation layer. The router was simplified after the first version had too many overlapping rules. The pattern was: I used AI tools to move fast on the parts that are mechanical, but never accept generated code on architectural decisions without reviewing the *why* behind it.

The result is a codebase where every meaningful design decision was made consciously, and where I can explain *why* each piece is the shape it is, including the parts AI tools helped write.
