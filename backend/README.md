# OracleX Backend 🎰

> *The story before it happens.*

AI-powered sports prediction platform. FastAPI + Groq streaming + Supabase + Reddit sentiment.

---

## Stack

| Layer | Tech | Cost |
|---|---|---|
| Inference | Groq (llama-3.3-70b + llama-3.1-8b) | Free (14,400 req/day) |
| Database | Supabase PostgreSQL | Free (500MB) |
| Cache | Redis via Upstash | Free (10k req/day) |
| Host | Railway | Free (500 hrs/mo) |
| Odds data | The Odds API | Free (500 req/mo) |
| Sentiment | Reddit API (asyncpraw) | Free |
| Stats/injuries | ESPN public API | Free |

---

## Quick Start

### 1. Clone + install

```bash
git clone <your-repo>
cd oraclex-backend

# Install Poetry if needed
curl -sSL https://install.python-poetry.org | python3 -

poetry install
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your keys (see below)
```

**Required keys:**
- `GROQ_API_KEY` — [console.groq.com](https://console.groq.com)
- `SUPABASE_URL` + `SUPABASE_ANON_KEY` + `SUPABASE_SERVICE_ROLE_KEY` — [supabase.com](https://supabase.com)
- `ODDS_API_KEY` — [the-odds-api.com](https://the-odds-api.com) (free tier)
- `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` — [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps)

**Optional (Redis caching):**
- `REDIS_URL` — [upstash.com](https://upstash.com) free tier. Leave blank for in-memory cache.

### 3. Run the Supabase migration

1. Open your Supabase project → **SQL Editor** → **New Query**
2. Paste the contents of `scripts/supabase_migration.sql`
3. Click **Run**

Also enable Realtime for live frontend updates:
- **Database → Replication → Source** → enable `predictions`, `games`, `odds_history`

### 4. Run locally

```bash
poetry run uvicorn app.main:app --reload --port 8000
```

Visit:
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

### 5. Trigger first data ingest

```bash
# Fetch odds for all tracked sports
curl -X POST http://localhost:8000/api/v1/odds/ingest

# List games
curl http://localhost:8000/api/v1/games

# Stream a prediction (replace with real game UUID)
curl -N http://localhost:8000/api/v1/predictions/stream/<game-id>
```

---

## API Reference

### Games
```
GET  /api/v1/games                     List games (filter: sport, status, hours_ahead)
GET  /api/v1/games/{id}                Get single game
GET  /api/v1/games/by-external/{id}    Lookup by The Odds API ID
```

### Odds
```
GET  /api/v1/odds/live/{game_id}        Current best odds
GET  /api/v1/odds/history/{game_id}     Line movement (for charts)
POST /api/v1/odds/ingest               Trigger manual ingest
GET  /api/v1/odds/sports               List tracked sports
```

### Predictions (⚡ the money endpoints)
```
GET  /api/v1/predictions/stream/{id}   SSE streaming narrative (connect with EventSource)
GET  /api/v1/predictions/{game_id}     Cached prediction (REST)
GET  /api/v1/predictions               List all predictions
GET  /api/v1/predictions/accuracy/stats  Win rate stats
POST /api/v1/predictions/batch-generate  Generate all upcoming
POST /api/v1/predictions/update-accuracy Update correctness after games
```

### Sentiment
```
GET  /api/v1/sentiment/{game_id}       Reddit sentiment for both teams
```

### Favorites (auth required)
```
GET    /api/v1/favorites?user_id=...   User's saved games
POST   /api/v1/favorites               Add favorite
DELETE /api/v1/favorites/{game_id}     Remove favorite
```

---

## SSE Streaming Format

Connect from the frontend:

```javascript
const es = new EventSource(`${API_BASE}/api/v1/predictions/stream/${gameId}`);

es.addEventListener('status',   e => console.log('Status:', e.data));
es.addEventListener('narrative', e => appendText(e.data));   // streaming chunks
es.addEventListener('metadata', e => {
  const meta = JSON.parse(e.data);
  // meta.predicted_winner, meta.confidence, meta.key_factors, meta.bet_recommendation
  renderPredictionCard(meta);
});
es.addEventListener('done',  () => es.close());
es.addEventListener('error', e => console.error(e.data));
```

---

## Scheduler Jobs

| Job | Default interval | What it does |
|---|---|---|
| `ingest_odds` | Every 10 min | Fetch all sports from The Odds API → upsert to DB |
| `update_accuracy` | Every 15 min | Check finalized games, mark predictions correct/incorrect |
| `generate_predictions` | Every 60 min | Auto-generate predictions for next 48hr games |

Override intervals in `.env`:
```
ODDS_FETCH_INTERVAL_MINUTES=5
PREDICTION_GENERATE_INTERVAL_MINUTES=30
```

---

## Deploy to Railway (free)

```bash
# Install Railway CLI
npm install -g @railway/cli
railway login

# Create project
railway init
railway up

# Set env vars
railway variables set GROQ_API_KEY=gsk_... SUPABASE_URL=https://...
```

Railway auto-detects `railway.toml` and uses nixpacks to build.

---

## Project Structure

```
app/
├── core/
│   ├── config.py          Pydantic settings
│   ├── database.py        Supabase clients
│   ├── cache.py           Redis + in-memory fallback
│   └── logging.py         structlog
├── models/
│   └── schemas.py         All Pydantic v2 models
├── services/
│   ├── odds_service.py    The Odds API ingest
│   ├── sentiment_service.py  Reddit + Groq scoring
│   ├── prediction_service.py ⚡ Groq streaming narrative
│   └── scheduler.py       APScheduler jobs
├── scrapers/
│   └── espn_scraper.py    Injuries + recent form
└── api/routes/
    ├── games.py
    ├── odds.py
    ├── predictions.py     SSE streaming endpoint
    ├── sentiment.py
    ├── favorites.py
    └── health.py
scripts/
└── supabase_migration.sql  Full DB schema + RLS + triggers
```

---

## Disclaimer

OracleX is an entertainment platform. All predictions are AI-generated analysis, not financial advice. Include responsible gambling notices in your frontend. Do not operate as a licensed sportsbook without proper licensing.
