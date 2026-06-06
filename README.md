# Creative Review Analysis API

AI-powered analysis of creative submissions using Claude. Two-tier processing: **Fast** (<3s, Haiku) for quick feedback and **Rich** (<60s, Opus) for deep evaluation.

## Quick Start

```bash
# 1. Install
make install

# 2. Configure
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY and API_KEYS

# 3. Run
make dev
# → http://localhost:8000

# 4. Test
curl -H "X-API-Key: devkey1" http://localhost:8000/health
```

## API

### Submit for Analysis

```bash
curl -X POST http://localhost:8000/submissions \
  -H "X-API-Key: devkey1" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Summer Campaign",
    "brief_text": "Vibrant ad for our summer sale targeting millennials.",
    "asset_urls": [],
    "tier": "auto"
  }'
```

### Check Status

```bash
curl -H "X-API-Key: devkey1" http://localhost:8000/submissions/{id}
```

### List All

```bash
curl -H "X-API-Key: devkey1" "http://localhost:8000/submissions?limit=20&offset=0"
```

### Dashboard

Open `http://localhost:8000/dashboard` in a browser for live updates.

## Tier Routing

| Rule | Tier |
|------|------|
| `tier=fast` | Fast |
| `tier=rich` | Rich |
| `asset_urls` count > 3 | Rich |
| `brief_text` > 2000 chars | Rich |
| Contains "campaign", "brand", "strategic", "launch" | Rich |
| Default | Fast |

## Fast Tier (Haiku)
- Synchronous response
- Single-pass analysis
- Model: `claude-haiku-4-5`
- Target: < $0.005 per call

## Rich Tier (Opus)
- Asynchronous (BackgroundTasks)
- Two-stage: extract → evaluate
- Model: `claude-opus-4-8` with adaptive thinking
- Target: < $0.05 per call
- Poll `GET /submissions/{id}` for completion

## Analysis Output

```json
{
  "summary": "...",
  "scores": [
    {"dimension": "concept", "score": 8, "rationale": "..."},
    {"dimension": "execution", "score": 7, "rationale": "..."},
    ...
  ],
  "strengths": [...],
  "improvements": [...],
  "target_audience": "...",
  "tone": "...",
  "recommendations": [...],
  "confidence": 0.85
}
```

## Running Tests

```bash
make test                         # unit + integration tests
EVAL_PROVIDER=mock make evals     # eval harness (no API key)
EVAL_PROVIDER=anthropic make evals # eval harness (real API)
```

## Docker

```bash
make docker-up
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Required for Anthropic provider |
| `API_KEYS` | `devkey1` | Comma-separated valid API keys |
| `DATABASE_URL` | SQLite | Database connection string |
| `FAST_MODEL` | `claude-haiku-4-5` | Model for fast tier |
| `RICH_MODEL` | `claude-opus-4-8` | Model for rich tier |
| `PROVIDER` | `anthropic` | `anthropic` or `mock` |
| `RATE_LIMIT_PER_MINUTE` | `60` | Requests per IP per minute |
