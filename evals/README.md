# Evaluation Harness

## Overview

15 fixture-based test cases that validate the Creative Review Analysis API against known inputs and expected outputs.

## Running Evals

```bash
# Against mock provider (fast, no API key needed) — also the default if EVAL_PROVIDER is unset
EVAL_PROVIDER=mock make evals

# Against the configured model pools (requires provider API keys in .env: ANTHROPIC_API_KEY / NIM_API_KEY)
EVAL_PROVIDER=strategy make evals

# Persist per-fixture results for comparison (writes evals/results/<fixture-id>.json)
EVAL_RESULTS_DIR=evals/results EVAL_PROVIDER=mock make evals

# Fetch real asset URLs instead of using synthetic 1x1 JPEG bytes
EVAL_FETCH_ASSETS=1 EVAL_PROVIDER=strategy make evals

# Run directly with pytest (equivalent to `make evals`, useful for -k / -x flags)
EVAL_PROVIDER=mock .venv/bin/pytest evals/test_evals.py -v

# Run inside Docker (see docker-compose.yml `evals` profile)
make docker-evals
```

- `EVAL_PROVIDER` selects the analysis backend: `mock` returns a fixed, deterministic `AnalysisResult` (no network calls — safe for CI); `strategy` runs the real `AnalysisStrategy` against the configured `FAST_POOL`/`RICH_POOL` model pools.
- `EVAL_RESULTS_DIR`, if set, makes each fixture write `<fixture-id>.json` (tier, duration, full result) to that directory for diffing across runs.
- `EVAL_FETCH_ASSETS=1` makes fixtures with `asset_urls` fetch real images via `fetch_images`; otherwise a synthetic 1x1 JPEG stands in per URL so the suite has no external dependency by default.

## Fixtures

| ID | Description | Tier | Provider |
|----|-------------|------|---------|
| tc-001 | Simple product ad brief — fast tier | fast | any |
| tc-002 | Minimal brief — fast tier edge case | fast | any |
| tc-003 | Strategic keyword in brief — auto routes to rich | rich | any |
| tc-004 | Explicit rich tier request | rich | any |
| tc-005 | Auto tier with short non-keyword brief — should route fast | fast | any |
| tc-006 | Empty brief — edge case | fast | any |
| tc-007 | Many asset URLs triggers rich tier auto-routing | rich | any |
| tc-008 | Long brief over 2000 chars auto-routes to rich | rich | any |
| tc-009 | Social media content brief — fast tier | fast | any |
| tc-010 | B2B SaaS brief with launch keyword | rich | any |
| tc-011 | Nonprofit appeal — fast tier | fast | any |
| tc-012 | Rebrand keyword triggers rich tier | rich | any |
| tc-013 | Rich content type routes to rich even with short text | rich | any |
| tc-014 | Explicit fast override wins over rich-looking brief | fast | any |
| tc-015 | Explicit rich override wins over minimal brief | rich | any |

## Assertions Per Fixture

- **Tier routing**: matches `expected_tier` if specified
- **Required fields**: all named fields are non-empty
- **Confidence**: meets `min_confidence` threshold
- **Score range**: all scores within `[1, 10]`
- **Score dimensions**: exactly the expected six scoring dimensions
- **Score count**: exactly 6 dimensions
- **Latency**: within `max_duration_ms`

By default, fixtures with `asset_urls` use synthetic image bytes so evals do not depend on network access. Set `EVAL_FETCH_ASSETS=1` when you intentionally want to exercise real URL fetching.

## Adding New Fixtures

Create `evals/fixtures/tc-NNN-description.json` following this schema:

```json
{
  "id": "tc-NNN",
  "description": "Human-readable description",
  "submission": {
    "title": "...",
    "brief_text": "...",
    "asset_urls": [],
    "tier": "auto|fast|rich",
    "content_type": "auto|tagline|headline|social_post|ad_copy|creative_brief|concept|campaign_brief|script_excerpt|treatment|pitch"
  },
  "expected": {
    "expected_tier": "fast|rich",
    "min_confidence": 0.5,
    "required_fields": ["summary", "scores", "strengths", "improvements", "recommendations"],
    "score_range": [1, 10],
    "num_scores": 6,
    "score_dimensions": ["concept", "execution", "audience_fit", "brand_alignment", "originality", "impact"],
    "max_duration_ms": 10000
  }
}
```

- `content_type` is optional and defaults to `"auto"`. `creative_brief`, `campaign_brief`, `script_excerpt`, `treatment`, and `pitch` are in `RICH_CONTENT_TYPES` (see `app/routing/router.py`) and force rich-tier routing regardless of brief length or keywords — use one of these when the fixture's purpose is to test content-type-driven routing (e.g. `tc-013`).
- `expected.score_dimensions` is optional and overrides the default six scoring dimensions (`concept`, `execution`, `audience_fit`, `brand_alignment`, `originality`, `impact`) that the harness checks for in `result.scores`.

Before the per-fixture analysis assertions run, `test_fixture_files_are_valid` validates every fixture's structure up front: unique `id`s across all fixtures, presence of `id`/`description`/`submission.title`, `submission.tier` (and `content_type`, if present) in their allowed sets, `asset_urls` is a list, the `submission` block constructs a valid `SubmissionCreate` (catching `ValidationError`), and — if present — `expected.expected_tier` is `fast`/`rich` and `expected.score_range == [1, 10]`. A malformed fixture fails fast here rather than producing a confusing failure deeper in the analysis assertions.
