# Evaluation Harness

## Overview

15 fixture-based test cases that validate the Creative Review Analysis API against known inputs and expected outputs.

## Running Evals

```bash
# Against mock provider (fast, no API key needed)
EVAL_PROVIDER=mock make evals

# Against the configured model pools (requires provider API keys)
EVAL_PROVIDER=strategy make evals

# Persist per-fixture results for comparison
EVAL_RESULTS_DIR=evals/results EVAL_PROVIDER=mock make evals

# Fetch real asset URLs instead of using synthetic image bytes
EVAL_FETCH_ASSETS=1 EVAL_PROVIDER=strategy make evals
```

## Fixtures

| ID | Description | Tier | Provider |
|----|-------------|------|---------|
| tc-001 | Simple product ad | fast | any |
| tc-002 | Minimal brief edge case | fast | any |
| tc-003 | Strategic keyword auto-routes rich | rich | any |
| tc-004 | Explicit rich tier | rich | any |
| tc-005 | Auto short non-keyword → fast | fast | any |
| tc-006 | Empty brief | fast | any |
| tc-007 | Many assets → rich | rich | any |
| tc-008 | Long brief → rich | rich | any |
| tc-009 | Social media brief | fast | any |
| tc-010 | B2B SaaS with launch keyword | rich | any |
| tc-011 | Nonprofit email appeal | fast | any |
| tc-012 | Rebrand keyword | rich | any |
| tc-013 | Rich content type | rich | any |
| tc-014 | Explicit fast override | fast | any |
| tc-015 | Explicit rich override | rich | any |

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
    "tier": "auto|fast|rich"
  },
  "expected": {
    "expected_tier": "fast|rich",
    "min_confidence": 0.5,
    "required_fields": ["summary", "scores", "strengths", "improvements", "recommendations"],
    "score_range": [1, 10],
    "num_scores": 6,
    "max_duration_ms": 10000
  }
}
```
