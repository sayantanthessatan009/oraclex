-- ============================================================
-- OracleX — Supabase PostgreSQL Schema
-- Run this in: Supabase Dashboard → SQL Editor → New Query
-- ============================================================

-- Enable UUID extension
create extension if not exists "uuid-ossp";

-- ─────────────────────────────────────────────────────────────
-- TABLE: games
-- ─────────────────────────────────────────────────────────────
create table if not exists public.games (
    id              uuid primary key default uuid_generate_v4(),
    external_id     text not null unique,          -- The Odds API game ID
    sport           text not null,                 -- e.g. "basketball_nba"
    home_team       text not null,
    away_team       text not null,
    game_time       timestamptz not null,
    status          text not null default 'upcoming'
                    check (status in ('upcoming', 'live', 'final', 'postponed')),
    home_score      integer,
    away_score      integer,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create index if not exists games_sport_idx       on public.games (sport);
create index if not exists games_status_idx      on public.games (status);
create index if not exists games_game_time_idx   on public.games (game_time);
create index if not exists games_external_id_idx on public.games (external_id);

-- ─────────────────────────────────────────────────────────────
-- TABLE: odds_history  (line-movement time series)
-- ─────────────────────────────────────────────────────────────
create table if not exists public.odds_history (
    id              bigint generated always as identity primary key,
    game_id         uuid not null references public.games (id) on delete cascade,
    bookmaker       text not null,                 -- draftkings, fanduel, etc.
    market          text not null                  -- h2h, spreads, totals
                    check (market in ('h2h', 'spreads', 'totals')),
    home_odds       double precision,              -- American odds / spread value
    away_odds       double precision,
    recorded_at     timestamptz not null default now()
);

create index if not exists odds_game_market_idx  on public.odds_history (game_id, market);
create index if not exists odds_recorded_at_idx  on public.odds_history (recorded_at desc);

-- Retain only 7 days of history (keeps free tier size manageable)
-- Run this as a scheduled Edge Function or manual cleanup
-- delete from public.odds_history where recorded_at < now() - interval '7 days';

-- ─────────────────────────────────────────────────────────────
-- TABLE: sentiment_scores
-- ─────────────────────────────────────────────────────────────
create table if not exists public.sentiment_scores (
    id              uuid primary key default uuid_generate_v4(),
    game_id         uuid not null unique references public.games (id) on delete cascade,
    home_team       text not null,
    away_team       text not null,
    home_score      double precision not null default 0.0
                    check (home_score between -1.0 and 1.0),
    away_score      double precision not null default 0.0
                    check (away_score between -1.0 and 1.0),
    home_signals    text[] default '{}',
    away_signals    text[] default '{}',
    home_summary    text default '',
    away_summary    text default '',
    edge            text default 'neutral',
    post_count_home integer default 0,
    post_count_away integer default 0,
    computed_at     timestamptz not null default now()
);

create index if not exists sentiment_game_idx on public.sentiment_scores (game_id);

-- ─────────────────────────────────────────────────────────────
-- TABLE: predictions  (the oracle's verdicts)
-- ─────────────────────────────────────────────────────────────
create table if not exists public.predictions (
    id                  uuid primary key default uuid_generate_v4(),
    game_id             uuid not null references public.games (id) on delete cascade,
    predicted_winner    text not null,
    confidence          double precision not null default 0.60
                        check (confidence between 0.0 and 1.0),
    narrative           text not null default '',       -- full Groq narrative
    key_factors         jsonb default '[]'::jsonb,      -- [{factor, weight, detail}]
    upset_watch         text default '',
    bet_recommendation  text default '',
    sentiment_data      jsonb,                          -- snapshot of sentiment at prediction time
    model_used          text not null default 'llama-3.3-70b-versatile',
    actual_winner       text,                           -- filled in after game ends
    was_correct         boolean,                        -- null = pending
    created_at          timestamptz not null default now()
);

create index if not exists predictions_game_idx      on public.predictions (game_id);
create index if not exists predictions_correct_idx   on public.predictions (was_correct);
create index if not exists predictions_created_idx   on public.predictions (created_at desc);
create index if not exists predictions_sport_idx     on public.predictions ((sentiment_data->>'sport'));

-- ─────────────────────────────────────────────────────────────
-- TABLE: user_favorites  (user's saved games)
-- ─────────────────────────────────────────────────────────────
create table if not exists public.user_favorites (
    id          uuid primary key default uuid_generate_v4(),
    user_id     uuid not null references auth.users (id) on delete cascade,
    game_id     uuid not null references public.games (id) on delete cascade,
    created_at  timestamptz not null default now(),
    unique (user_id, game_id)
);

create index if not exists favorites_user_idx on public.user_favorites (user_id);

-- ─────────────────────────────────────────────────────────────
-- TABLE: user_profiles  (public user stats)
-- ─────────────────────────────────────────────────────────────
create table if not exists public.user_profiles (
    id              uuid primary key references auth.users (id) on delete cascade,
    display_name    text,
    avatar_url      text,
    total_picks     integer not null default 0,
    correct_picks   integer not null default 0,
    favorite_sport  text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- ─────────────────────────────────────────────────────────────
-- TRIGGERS
-- ─────────────────────────────────────────────────────────────

-- Auto-update updated_at on games
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists games_updated_at on public.games;
create trigger games_updated_at
    before update on public.games
    for each row execute function public.set_updated_at();

drop trigger if exists profiles_updated_at on public.user_profiles;
create trigger profiles_updated_at
    before update on public.user_profiles
    for each row execute function public.set_updated_at();

-- Auto-create user profile on sign-up
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer as $$
begin
    insert into public.user_profiles (id, display_name)
    values (new.id, coalesce(new.raw_user_meta_data->>'full_name', split_part(new.email, '@', 1)));
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();

-- Auto-set was_correct when actual_winner is set on predictions
create or replace function public.compute_prediction_correctness()
returns trigger language plpgsql as $$
begin
    if new.actual_winner is not null and old.actual_winner is null then
        new.was_correct = (lower(new.actual_winner) = lower(new.predicted_winner));
    end if;
    return new;
end;
$$;

drop trigger if exists prediction_correctness on public.predictions;
create trigger prediction_correctness
    before update on public.predictions
    for each row execute function public.compute_prediction_correctness();

-- ─────────────────────────────────────────────────────────────
-- ROW LEVEL SECURITY
-- ─────────────────────────────────────────────────────────────

-- Games, odds, predictions, sentiment — public read
alter table public.games enable row level security;
create policy "games_public_read" on public.games
    for select using (true);
create policy "games_service_write" on public.games
    for all using (auth.role() = 'service_role');

alter table public.odds_history enable row level security;
create policy "odds_public_read" on public.odds_history
    for select using (true);
create policy "odds_service_write" on public.odds_history
    for all using (auth.role() = 'service_role');

alter table public.predictions enable row level security;
create policy "predictions_public_read" on public.predictions
    for select using (true);
create policy "predictions_service_write" on public.predictions
    for all using (auth.role() = 'service_role');

alter table public.sentiment_scores enable row level security;
create policy "sentiment_public_read" on public.sentiment_scores
    for select using (true);
create policy "sentiment_service_write" on public.sentiment_scores
    for all using (auth.role() = 'service_role');

-- Favorites — scoped to owner
alter table public.user_favorites enable row level security;
create policy "favorites_own_read" on public.user_favorites
    for select using (auth.uid() = user_id);
create policy "favorites_own_insert" on public.user_favorites
    for insert with check (auth.uid() = user_id);
create policy "favorites_own_delete" on public.user_favorites
    for delete using (auth.uid() = user_id);

-- User profiles — public read, own write
alter table public.user_profiles enable row level security;
create policy "profiles_public_read" on public.user_profiles
    for select using (true);
create policy "profiles_own_update" on public.user_profiles
    for update using (auth.uid() = id);

-- ─────────────────────────────────────────────────────────────
-- VIEWS  (handy for frontend queries)
-- ─────────────────────────────────────────────────────────────

-- Games with latest prediction and sentiment
create or replace view public.games_with_predictions as
select
    g.id,
    g.external_id,
    g.sport,
    g.home_team,
    g.away_team,
    g.game_time,
    g.status,
    g.home_score,
    g.away_score,
    p.id               as prediction_id,
    p.predicted_winner,
    p.confidence,
    p.narrative,
    p.key_factors,
    p.upset_watch,
    p.bet_recommendation,
    p.was_correct,
    p.created_at       as predicted_at,
    s.home_score       as home_sentiment,
    s.away_score       as away_sentiment,
    s.edge             as sentiment_edge
from public.games g
left join lateral (
    select * from public.predictions
    where game_id = g.id
    order by created_at desc
    limit 1
) p on true
left join public.sentiment_scores s on s.game_id = g.id;

-- Overall accuracy leaderboard view
create or replace view public.accuracy_leaderboard as
select
    sport,
    count(*)                                                         as total,
    count(*) filter (where was_correct = true)                       as correct,
    count(*) filter (where was_correct = false)                      as incorrect,
    count(*) filter (where was_correct is null)                      as pending,
    round(
        count(*) filter (where was_correct = true)::numeric
        / nullif(count(*) filter (where was_correct is not null), 0) * 100,
        1
    )                                                                as accuracy_pct
from public.predictions
group by sport
order by accuracy_pct desc nulls last;

-- ─────────────────────────────────────────────────────────────
-- REALTIME  (enable for live frontend updates)
-- ─────────────────────────────────────────────────────────────
-- Run in Supabase Dashboard → Database → Replication
-- Or uncomment below:
--
-- begin;
--   select supabase_realtime.quote_wal2json('public.predictions');
--   select supabase_realtime.quote_wal2json('public.games');
--   select supabase_realtime.quote_wal2json('public.odds_history');
-- commit;

-- ─────────────────────────────────────────────────────────────
-- SEED DATA  (optional — one test game for local dev)
-- ─────────────────────────────────────────────────────────────
-- insert into public.games (external_id, sport, home_team, away_team, game_time)
-- values (
--     'test-game-001',
--     'basketball_nba',
--     'Los Angeles Lakers',
--     'Boston Celtics',
--     now() + interval '2 days'
-- );
