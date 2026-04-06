-- ============================================================
-- UTOPIA — Complete Database Schema v1.0
-- PostgreSQL 15+ / Supabase
--
-- Conventions:
--   · All PKs:        UUID v4 via gen_random_uuid()
--   · All timestamps: TIMESTAMPTZ (UTC)
--   · Soft deletes:   deleted_at column where history matters
--   · Money/lamports: BIGINT (no floats for currency)
--   · XP / points:    INTEGER (never FLOAT)
--   · Names:          snake_case throughout
-- ============================================================

-- ─── EXTENSIONS ──────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ─── SCHEMAS ─────────────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS public;
CREATE SCHEMA IF NOT EXISTS agent;
CREATE SCHEMA IF NOT EXISTS analytics;

-- ─── ENUMS ───────────────────────────────────────────────────────────────────

CREATE TYPE user_level AS ENUM (
    'bronze', 'silver', 'gold', 'elite'
);

CREATE TYPE mission_type AS ENUM (
    'onboarding',   -- one-time setup steps
    'daily',        -- resets each UTC day
    'clipping',     -- content creation / clips / memes
    'special',      -- offerwall, video ads, affiliate
    'referral'      -- invite friend who must stake minimum
);

CREATE TYPE mission_status AS ENUM (
    'active', 'paused', 'archived'
);

CREATE TYPE completion_status AS ENUM (
    'pending',   -- awaiting verification
    'verified',  -- confirmed
    'rejected',  -- failed verification
    'expired',   -- window passed before verification
    'revoked'    -- was verified, later reversed (e.g. refund)
);

CREATE TYPE prize_stack_model AS ENUM (
    'fixed',           -- creator deposits fixed amount upfront
    'fixed_plus_fees', -- fixed floor + ongoing fee routing
    'fees_only'        -- 100% from trading fee routing, zero upfront
);

CREATE TYPE competition_status AS ENUM (
    'draft', 'active', 'ended', 'distributed', 'cancelled'
);

CREATE TYPE tx_type AS ENUM (
    'prize_deposit',
    'prize_payout',
    'fee_routing_in',
    'agent_funding',
    'community_pool_in',
    'creator_fee_out',
    'burn',
    'stake_lock',
    'stake_unlock',
    'referral_bonus'
);

CREATE TYPE social_platform AS ENUM (
    'x', 'tiktok', 'youtube', 'instagram', 'twitch'
);

CREATE TYPE verification_method AS ENUM (
    'api_x',             -- SocialData.tools / X API call
    'offerwall_callback',-- server-side postback from provider
    'webhook',           -- Shopify/WooCommerce webhook
    'onchain',           -- Solana transaction verified
    'manual',            -- admin review
    'self_report'        -- unverified user claim (flagged)
);

CREATE TYPE agent_action_type AS ENUM (
    'post_x', 'reply_x', 'youtube_short',
    'community_reward', 'wallet_spend', 'donation_request'
);


-- ============================================================
-- DOMAIN 1 — USERS & IDENTITY
-- ============================================================

CREATE TABLE public.users (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    auth_id             UUID        UNIQUE NOT NULL,  -- Supabase auth.users.id

    -- X (Twitter) identity
    x_id                TEXT        UNIQUE,
    x_handle            TEXT,
    x_display_name      TEXT,
    x_avatar_url        TEXT,
    x_followers_count   INTEGER     DEFAULT 0,
    x_verified          BOOLEAN     DEFAULT FALSE,
    x_oauth_token       TEXT,       -- stored encrypted
    x_oauth_secret      TEXT,       -- stored encrypted
    x_token_expires_at  TIMESTAMPTZ,

    -- Solana wallet
    wallet_address      TEXT        UNIQUE,
    wallet_verified_at  TIMESTAMPTZ,
    wallet_stake_amount BIGINT      DEFAULT 0, -- total lamports staked across all communities

    -- Profile extras
    display_name        TEXT,
    avatar_url          TEXT,
    bio                 TEXT        CHECK (char_length(bio) <= 280),

    -- Anti-Sybil
    trust_score         SMALLINT    DEFAULT 50 CHECK (trust_score BETWEEN 0 AND 100),
    sybil_flags         SMALLINT    DEFAULT 0,
    -- bitmask: 1=multi-wallet 2=bot-pattern 4=burst-activity 8=offerwall-abuse
    is_banned           BOOLEAN     DEFAULT FALSE,
    banned_reason       TEXT,
    banned_at           TIMESTAMPTZ,

    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    last_active_at      TIMESTAMPTZ DEFAULT NOW(),
    deleted_at          TIMESTAMPTZ
);

CREATE INDEX idx_users_x_id        ON public.users(x_id);
CREATE INDEX idx_users_x_handle    ON public.users(x_handle);
CREATE INDEX idx_users_wallet       ON public.users(wallet_address);
CREATE INDEX idx_users_trust        ON public.users(trust_score);
CREATE INDEX idx_users_last_active  ON public.users(last_active_at DESC);


-- ─── Daily login streaks ──────────────────────────────────────────────────────
CREATE TABLE public.user_login_streaks (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID        NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    community_id        UUID        NOT NULL,   -- FK added below after communities exists
    current_streak      INTEGER     DEFAULT 0,
    longest_streak      INTEGER     DEFAULT 0,
    last_checkin_date   DATE,                   -- UTC
    total_checkins      INTEGER     DEFAULT 0,
    xp_earned_total     INTEGER     DEFAULT 0,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, community_id)
);

CREATE INDEX idx_streaks_user      ON public.user_login_streaks(user_id);
CREATE INDEX idx_streaks_community ON public.user_login_streaks(community_id);


-- ============================================================
-- DOMAIN 2 — CREATORS & COMMUNITIES
-- ============================================================

CREATE TABLE public.creators (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID        UNIQUE NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,

    -- Subscription
    plan                TEXT        DEFAULT 'free' CHECK (plan IN ('free','premium')),
    plan_started_at     TIMESTAMPTZ,
    plan_expires_at     TIMESTAMPTZ,
    paid_in_token       BOOLEAN     DEFAULT FALSE, -- $UTOPIA payment → 20% discount

    -- Revenue ledger
    total_revenue_usd   NUMERIC(12,4) DEFAULT 0,
    total_paid_out_usd  NUMERIC(12,4) DEFAULT 0,
    pending_payout_usd  NUMERIC(12,4) DEFAULT 0,

    -- Referral programme
    referral_code       TEXT        UNIQUE DEFAULT substring(gen_random_uuid()::text, 1, 8),
    referred_by         UUID        REFERENCES public.creators(id),
    referral_count      INTEGER     DEFAULT 0,

    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_creators_user ON public.creators(user_id);
CREATE INDEX idx_creators_plan ON public.creators(plan);
CREATE INDEX idx_creators_ref  ON public.creators(referral_code);


CREATE TABLE public.communities (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id          UUID        NOT NULL REFERENCES public.creators(id) ON DELETE RESTRICT,

    -- Identity
    name                TEXT        NOT NULL CHECK (char_length(name) BETWEEN 2 AND 64),
    slug                TEXT        UNIQUE NOT NULL,
    description         TEXT        CHECK (char_length(description) <= 500),
    avatar_url          TEXT,
    cover_url           TEXT,
    accent_color        TEXT        DEFAULT '#E1FF00' CHECK (accent_color ~ '^#[0-9A-Fa-f]{6}$'),

    -- Social links
    x_community_url     TEXT,
    website_url         TEXT,
    pump_fun_url        TEXT,
    telegram_url        TEXT,

    -- Token
    token_address       TEXT,       -- Solana SPL mint address
    token_symbol        TEXT,
    token_name          TEXT,
    token_decimals      SMALLINT    DEFAULT 6,
    launch_platform     TEXT        CHECK (launch_platform IN ('pump_fun','bags_fm','other','none')),

    -- Fee routing
    fee_routing_enabled BOOLEAN     DEFAULT FALSE,
    fee_wallet_address  TEXT,       -- UTOPIA escrow wallet for this community
    fee_pct_community   SMALLINT    DEFAULT 50 CHECK (fee_pct_community BETWEEN 0 AND 100),
    fee_pct_agent       SMALLINT    DEFAULT 20 CHECK (fee_pct_agent    BETWEEN 0 AND 100),
    fee_pct_creator     SMALLINT    DEFAULT 30 CHECK (fee_pct_creator  BETWEEN 0 AND 100),
    CONSTRAINT chk_fee_pct_sum CHECK (fee_pct_community + fee_pct_agent + fee_pct_creator = 100),

    -- Denormalised counters
    member_count                INTEGER DEFAULT 0,
    total_xp_distributed        INTEGER DEFAULT 0,
    total_missions_completed    INTEGER DEFAULT 0,

    is_active           BOOLEAN     DEFAULT TRUE,
    is_featured         BOOLEAN     DEFAULT FALSE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    deleted_at          TIMESTAMPTZ
);

CREATE INDEX idx_communities_creator   ON public.communities(creator_id);
CREATE INDEX idx_communities_slug      ON public.communities(slug);
CREATE INDEX idx_communities_token     ON public.communities(token_address);
CREATE INDEX idx_communities_active    ON public.communities(is_active) WHERE is_active = TRUE;
CREATE INDEX idx_communities_featured  ON public.communities(is_featured) WHERE is_featured = TRUE;
CREATE INDEX idx_communities_name_trgm ON public.communities USING gin(name gin_trgm_ops);
CREATE INDEX idx_communities_token_trgm ON public.communities USING gin(token_address gin_trgm_ops);

-- Backfill FK on streaks now that communities exists
ALTER TABLE public.user_login_streaks
    ADD CONSTRAINT fk_streaks_community
    FOREIGN KEY (community_id) REFERENCES public.communities(id) ON DELETE CASCADE;


CREATE TABLE public.community_members (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    community_id        UUID        NOT NULL REFERENCES public.communities(id) ON DELETE CASCADE,
    user_id             UUID        NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,

    -- Progression
    level               user_level  DEFAULT 'bronze',
    xp_total            INTEGER     DEFAULT 0 CHECK (xp_total >= 0),
    xp_this_week        INTEGER     DEFAULT 0,
    xp_this_month       INTEGER     DEFAULT 0,
    xp_week_reset_at    TIMESTAMPTZ DEFAULT date_trunc('week',  NOW()),
    xp_month_reset_at   TIMESTAMPTZ DEFAULT date_trunc('month', NOW()),

    -- Boost (from token burn or level perks)
    xp_multiplier       NUMERIC(4,2) DEFAULT 1.00 CHECK (xp_multiplier BETWEEN 1.00 AND 5.00),
    xp_multiplier_expires_at TIMESTAMPTZ,

    -- Mission tracking
    missions_completed  INTEGER     DEFAULT 0,
    missions_today      INTEGER     DEFAULT 0,
    missions_today_date DATE        DEFAULT CURRENT_DATE,

    -- Competition eligibility
    is_staked           BOOLEAN     DEFAULT FALSE,
    stake_amount        BIGINT      DEFAULT 0, -- lamports staked for this community
    staked_at           TIMESTAMPTZ,

    -- Social metrics
    referral_count      INTEGER     DEFAULT 0,
    clipping_score      NUMERIC(6,2) DEFAULT 0,

    joined_at           TIMESTAMPTZ DEFAULT NOW(),
    last_mission_at     TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (community_id, user_id)
);

CREATE INDEX idx_members_community  ON public.community_members(community_id);
CREATE INDEX idx_members_user       ON public.community_members(user_id);
CREATE INDEX idx_members_xp_total   ON public.community_members(community_id, xp_total DESC);
CREATE INDEX idx_members_xp_week    ON public.community_members(community_id, xp_this_week DESC);
CREATE INDEX idx_members_level      ON public.community_members(level);


-- ============================================================
-- DOMAIN 3 — MISSIONS
-- ============================================================

CREATE TABLE public.mission_templates (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    community_id        UUID        NOT NULL REFERENCES public.communities(id) ON DELETE CASCADE,

    title               TEXT        NOT NULL CHECK (char_length(title) BETWEEN 3 AND 120),
    description         TEXT        CHECK (char_length(description) <= 500),
    type                mission_type NOT NULL,
    status              mission_status DEFAULT 'active',

    -- Reward (XP only — SOL prizes live in competitions)
    xp_reward           INTEGER     NOT NULL CHECK (xp_reward BETWEEN 1 AND 10000),
    xp_reward_elite     INTEGER,    -- optional bonus XP for Elite members

    -- Verification
    verification_method verification_method NOT NULL,
    verification_data   JSONB       DEFAULT '{}',
    -- Examples:
    --   daily post:     {"action":"post","keyword":"$UTOPIA","min_length":10}
    --   retweet:        {"action":"retweet","tweet_id":null}
    --   clip:           {"platform":"x","min_duration_sec":15,"quality_threshold":0.6}
    --   offerwall:      {"provider":"offertoro","offer_id":"12345","payout_usd":0.40}
    --   video_ad:       {"provider":"adgate","video_id":"abc","duration_sec":30}
    --   referral:       {"requires_stake":true,"min_stake_lamports":5000000}
    --   onchain_stake:  {"action":"stake","min_lamports":5000000}

    -- Scheduling
    is_daily            BOOLEAN     DEFAULT FALSE,
    daily_reset_hour    SMALLINT    DEFAULT 0 CHECK (daily_reset_hour BETWEEN 0 AND 23),
    available_from      TIMESTAMPTZ,
    available_until     TIMESTAMPTZ,
    max_completions_total       INTEGER,  -- NULL = unlimited
    max_completions_per_user    INTEGER   DEFAULT 1,
    max_completions_per_day     INTEGER,

    -- Display
    icon                TEXT        DEFAULT 'zap',
    sort_order          SMALLINT    DEFAULT 0,
    is_featured         BOOLEAN     DEFAULT FALSE,

    -- Offerwall
    offerwall_provider  TEXT        CHECK (offerwall_provider IN ('offertoro','adgate','freecash','other')),
    offerwall_offer_id  TEXT,
    offerwall_payout_usd NUMERIC(8,4), -- gross payout (creator gets 30%, UTOPIA 70%)

    -- Affiliate
    affiliate_url       TEXT,
    affiliate_provider  TEXT,

    -- Stats (denormalised)
    total_completions   INTEGER     DEFAULT 0,
    total_xp_awarded    INTEGER     DEFAULT 0,

    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_mission_tmpl_community ON public.mission_templates(community_id);
CREATE INDEX idx_mission_tmpl_type      ON public.mission_templates(type);
CREATE INDEX idx_mission_tmpl_active    ON public.mission_templates(community_id, type, sort_order)
    WHERE status = 'active';


CREATE TABLE public.mission_completions (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id          UUID        NOT NULL REFERENCES public.mission_templates(id) ON DELETE CASCADE,
    user_id             UUID        NOT NULL REFERENCES public.users(id)             ON DELETE CASCADE,
    community_id        UUID        NOT NULL REFERENCES public.communities(id)       ON DELETE CASCADE,

    status              completion_status DEFAULT 'pending',

    proof_data          JSONB       DEFAULT '{}',
    -- Examples:
    --   {"tweet_id":"1234","tweet_url":"https://x.com/..."}
    --   {"tweet_id":"...","retweeted_id":"..."}
    --   {"tweet_id":"...","views":1240,"likes":48,"quality_score":0.74}
    --   {"transaction_id":"OFW-abc123","provider":"offertoro"}
    --   {"tx_signature":"5KKkL..."}

    verification_method verification_method,
    verified_at         TIMESTAMPTZ,
    verified_by         TEXT,       -- 'system' | 'api_x' | 'admin:{user_id}'
    rejection_reason    TEXT,

    xp_awarded          INTEGER     DEFAULT 0,
    xp_multiplier_applied NUMERIC(4,2) DEFAULT 1.00,

    completion_date     DATE        DEFAULT CURRENT_DATE,

    ip_hash             TEXT,       -- hashed, not raw
    user_agent_hash     TEXT,

    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_completion_mission    ON public.mission_completions(mission_id);
CREATE INDEX idx_completion_user       ON public.mission_completions(user_id);
CREATE INDEX idx_completion_community  ON public.mission_completions(community_id);
CREATE INDEX idx_completion_status     ON public.mission_completions(status);
CREATE INDEX idx_completion_date       ON public.mission_completions(completion_date DESC);
CREATE INDEX idx_completion_pending    ON public.mission_completions(created_at ASC)
    WHERE status = 'pending';
-- Prevent duplicate daily completions
CREATE UNIQUE INDEX idx_completion_daily_unique
    ON public.mission_completions(mission_id, user_id, completion_date)
    WHERE status NOT IN ('rejected','expired','revoked');


-- ============================================================
-- DOMAIN 4 — COMPETITIONS & PRIZE POOLS
-- ============================================================

CREATE TABLE public.competitions (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    community_id        UUID        NOT NULL REFERENCES public.communities(id) ON DELETE CASCADE,

    title               TEXT        NOT NULL CHECK (char_length(title) BETWEEN 3 AND 120),
    description         TEXT,
    rules               TEXT,

    prize_stack_model   prize_stack_model NOT NULL,
    status              competition_status DEFAULT 'draft',

    -- Prize amounts
    prize_pool_sol      BIGINT      DEFAULT 0, -- lamports
    prize_pool_token    BIGINT      DEFAULT 0, -- SPL token units
    prize_currency      TEXT        DEFAULT 'SOL',

    -- Fixed deposit (models: fixed, fixed_plus_fees)
    fixed_deposit_amount BIGINT     DEFAULT 0,
    fixed_deposit_tx    TEXT,
    fixed_deposit_at    TIMESTAMPTZ,

    -- Fee accumulation (models: fixed_plus_fees, fees_only)
    fee_accumulated_sol BIGINT      DEFAULT 0,
    fee_last_updated_at TIMESTAMPTZ,

    -- Smart contract escrow
    escrow_wallet       TEXT,
    escrow_program_id   TEXT,

    -- UTOPIA service fee (5% of total pool)
    utopia_fee_pct      NUMERIC(4,2) DEFAULT 5.00,
    utopia_fee_amount   BIGINT      DEFAULT 0,
    utopia_fee_paid     BOOLEAN     DEFAULT FALSE,

    -- Scoring config
    scored_mission_types mission_type[] DEFAULT ARRAY['daily','clipping','special','referral']::mission_type[],
    min_xp_to_enter     INTEGER     DEFAULT 0,
    requires_stake      BOOLEAN     DEFAULT TRUE,

    -- Timing
    starts_at           TIMESTAMPTZ NOT NULL,
    ends_at             TIMESTAMPTZ NOT NULL,
    distribution_at     TIMESTAMPTZ,
    CONSTRAINT chk_competition_dates CHECK (ends_at > starts_at),

    -- Results
    winner_count        SMALLINT    DEFAULT 3,
    total_participants  INTEGER     DEFAULT 0,
    distribution_tx     TEXT,

    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_competition_community ON public.competitions(community_id);
CREATE INDEX idx_competition_status    ON public.competitions(status);
CREATE INDEX idx_competition_dates     ON public.competitions(starts_at, ends_at);
CREATE INDEX idx_competition_active    ON public.competitions(community_id, ends_at DESC)
    WHERE status = 'active';


CREATE TABLE public.competition_prize_tiers (
    id              UUID     PRIMARY KEY DEFAULT gen_random_uuid(),
    competition_id  UUID     NOT NULL REFERENCES public.competitions(id) ON DELETE CASCADE,
    rank            SMALLINT NOT NULL CHECK (rank >= 1),
    pct_of_pool     NUMERIC(5,2) NOT NULL CHECK (pct_of_pool > 0 AND pct_of_pool <= 100),
    label           TEXT,    -- '1st Place', 'Runner Up', etc.
    UNIQUE (competition_id, rank)
);


CREATE TABLE public.competition_entries (
    id              UUID     PRIMARY KEY DEFAULT gen_random_uuid(),
    competition_id  UUID     NOT NULL REFERENCES public.competitions(id) ON DELETE CASCADE,
    user_id         UUID     NOT NULL REFERENCES public.users(id)        ON DELETE CASCADE,
    community_id    UUID     NOT NULL REFERENCES public.communities(id),

    xp_competition  INTEGER  DEFAULT 0, -- XP earned within the competition window only
    rank_current    INTEGER,
    rank_final      INTEGER,

    -- Prize
    prize_tier      SMALLINT,
    prize_amount_sol   BIGINT DEFAULT 0,
    prize_amount_token BIGINT DEFAULT 0,
    prize_paid      BOOLEAN  DEFAULT FALSE,
    prize_tx        TEXT,
    prize_paid_at   TIMESTAMPTZ,

    entered_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (competition_id, user_id)
);

CREATE INDEX idx_entry_competition  ON public.competition_entries(competition_id);
CREATE INDEX idx_entry_user         ON public.competition_entries(user_id);
CREATE INDEX idx_entry_rank         ON public.competition_entries(competition_id, xp_competition DESC);
CREATE INDEX idx_entry_unpaid       ON public.competition_entries(competition_id)
    WHERE prize_paid = FALSE AND prize_tier IS NOT NULL;


-- ============================================================
-- DOMAIN 5 — FEE ROUTING
-- ============================================================

CREATE TABLE public.fee_routing_configs (
    id                  UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    community_id        UUID    UNIQUE NOT NULL REFERENCES public.communities(id) ON DELETE CASCADE,

    platform            TEXT    NOT NULL CHECK (platform IN ('pump_fun','bags_fm','other')),
    token_mint          TEXT    NOT NULL,
    creator_wallet      TEXT    NOT NULL,

    community_escrow_wallet TEXT NOT NULL,
    agent_wallet            TEXT NOT NULL,
    creator_split_wallet    TEXT NOT NULL,

    pct_community       SMALLINT NOT NULL DEFAULT 50,
    pct_agent           SMALLINT NOT NULL DEFAULT 20,
    pct_creator         SMALLINT NOT NULL DEFAULT 30,
    CONSTRAINT chk_routing_pct CHECK (pct_community + pct_agent + pct_creator = 100),

    agent_pct_burn      SMALLINT DEFAULT 50,
    agent_pct_budget    SMALLINT DEFAULT 50,
    CONSTRAINT chk_agent_pct CHECK (agent_pct_burn + agent_pct_budget = 100),

    is_configured       BOOLEAN  DEFAULT FALSE,
    configured_at       TIMESTAMPTZ,
    last_fee_received_at TIMESTAMPTZ,

    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);


CREATE TABLE public.fee_routing_events (
    id              UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    config_id       UUID    NOT NULL REFERENCES public.fee_routing_configs(id),
    community_id    UUID    NOT NULL REFERENCES public.communities(id),

    tx_signature    TEXT    UNIQUE NOT NULL,
    block_time      TIMESTAMPTZ NOT NULL,
    slot            BIGINT,

    total_amount        BIGINT NOT NULL,
    community_amount    BIGINT NOT NULL,
    agent_amount        BIGINT NOT NULL,
    creator_amount      BIGINT NOT NULL,
    burn_amount         BIGINT DEFAULT 0,

    processed       BOOLEAN DEFAULT FALSE,
    processed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_fee_event_community  ON public.fee_routing_events(community_id);
CREATE INDEX idx_fee_event_time       ON public.fee_routing_events(block_time DESC);
CREATE INDEX idx_fee_event_unprocessed ON public.fee_routing_events(created_at ASC)
    WHERE processed = FALSE;


CREATE TABLE public.token_transactions (
    id              UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    community_id    UUID    REFERENCES public.communities(id),
    user_id         UUID    REFERENCES public.users(id),
    competition_id  UUID    REFERENCES public.competitions(id),

    type            tx_type NOT NULL,
    amount_lamports BIGINT  NOT NULL,
    direction       CHAR(1) NOT NULL CHECK (direction IN ('+','-')),
    currency        TEXT    DEFAULT 'SOL',

    tx_signature    TEXT,
    from_wallet     TEXT,
    to_wallet       TEXT,
    memo            TEXT,   -- on-chain memo field

    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_token_tx_community  ON public.token_transactions(community_id);
CREATE INDEX idx_token_tx_user       ON public.token_transactions(user_id);
CREATE INDEX idx_token_tx_competition ON public.token_transactions(competition_id);
CREATE INDEX idx_token_tx_type       ON public.token_transactions(type);
CREATE INDEX idx_token_tx_time       ON public.token_transactions(created_at DESC);


-- ============================================================
-- DOMAIN 6 — OFFERWALL & SPECIAL MISSIONS
-- ============================================================

CREATE TABLE public.offerwall_providers (
    id              UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT    UNIQUE NOT NULL,
    api_key         TEXT,   -- encrypted at rest
    postback_secret TEXT,   -- for HMAC verification of callbacks
    is_active       BOOLEAN DEFAULT TRUE,
    base_url        TEXT,
    config          JSONB   DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);


CREATE TABLE public.offerwall_completions (
    id                      UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id             UUID    NOT NULL REFERENCES public.offerwall_providers(id),
    mission_completion_id   UUID    REFERENCES public.mission_completions(id),
    user_id                 UUID    NOT NULL REFERENCES public.users(id),
    community_id            UUID    NOT NULL REFERENCES public.communities(id),

    provider_transaction_id TEXT    NOT NULL,
    offer_id                TEXT,
    offer_name              TEXT,
    payout_usd              NUMERIC(8,4),

    utopia_share_usd        NUMERIC(8,4), -- 70%
    creator_share_usd       NUMERIC(8,4), -- 30%
    creator_paid            BOOLEAN DEFAULT FALSE,
    creator_paid_at         TIMESTAMPTZ,

    raw_postback            JSONB,
    ip_address              TEXT,
    is_fraud_flagged        BOOLEAN DEFAULT FALSE,

    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_offerwall_user      ON public.offerwall_completions(user_id);
CREATE INDEX idx_offerwall_community ON public.offerwall_completions(community_id);
CREATE INDEX idx_offerwall_unpaid    ON public.offerwall_completions(community_id)
    WHERE creator_paid = FALSE;
CREATE UNIQUE INDEX idx_offerwall_provider_txn
    ON public.offerwall_completions(provider_id, provider_transaction_id);


-- ============================================================
-- DOMAIN 7 — AI AGENT
-- ============================================================

CREATE TABLE agent.wallet_state (
    id                  UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    wallet_address      TEXT    NOT NULL UNIQUE,
    network             TEXT    DEFAULT 'mainnet-beta',
    balance_lamports    BIGINT  DEFAULT 0,
    total_received      BIGINT  DEFAULT 0,
    total_spent         BIGINT  DEFAULT 0,
    total_burned        BIGINT  DEFAULT 0,
    last_synced_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);


CREATE TABLE agent.actions (
    id              UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    action_type     agent_action_type NOT NULL,
    status          TEXT    DEFAULT 'pending'
                    CHECK (status IN ('pending','success','failed','skipped')),

    -- Content
    content_text    TEXT,
    content_url     TEXT,
    platform        social_platform,

    -- X metrics
    x_tweet_id      TEXT,
    x_impressions   INTEGER,
    x_likes         INTEGER,
    x_retweets      INTEGER,
    x_replies       INTEGER,

    -- YouTube metrics
    yt_video_id     TEXT,
    yt_views        INTEGER,
    yt_likes        INTEGER,

    -- Wallet action
    tx_signature    TEXT,
    amount_lamports BIGINT,
    recipient_wallet TEXT,
    spend_reason    TEXT,   -- becomes on-chain memo
    requires_approval BOOLEAN DEFAULT FALSE,
    approved_by     TEXT,   -- NULL = fully autonomous
    approved_at     TIMESTAMPTZ,

    -- LLM metadata
    decision_context JSONB  DEFAULT '{}',
    llm_model       TEXT    DEFAULT 'claude-sonnet-4-20250514',
    tokens_used     INTEGER,

    created_at      TIMESTAMPTZ DEFAULT NOW(),
    executed_at     TIMESTAMPTZ,
    error_message   TEXT
);

CREATE INDEX idx_agent_action_type   ON agent.actions(action_type);
CREATE INDEX idx_agent_action_status ON agent.actions(status);
CREATE INDEX idx_agent_action_time   ON agent.actions(created_at DESC);


CREATE TABLE agent.content_queue (
    id              UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    action_type     agent_action_type NOT NULL,
    platform        social_platform NOT NULL,
    priority        SMALLINT DEFAULT 5 CHECK (priority BETWEEN 1 AND 10),
    scheduled_for   TIMESTAMPTZ NOT NULL,
    content_text    TEXT,
    content_metadata JSONB DEFAULT '{}',
    status          TEXT    DEFAULT 'queued'
                    CHECK (status IN ('queued','processing','done','failed','cancelled')),
    action_id       UUID    REFERENCES agent.actions(id),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_queue_scheduled ON agent.content_queue(scheduled_for ASC)
    WHERE status = 'queued';
CREATE INDEX idx_queue_priority  ON agent.content_queue(priority DESC, scheduled_for ASC)
    WHERE status = 'queued';


-- ============================================================
-- DOMAIN 8 — AIRDROP
-- ============================================================

CREATE TABLE public.airdrop_campaigns (
    id                  UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT    NOT NULL,
    description         TEXT,
    token_mint          TEXT    NOT NULL,
    total_allocation    BIGINT  NOT NULL,
    remaining_allocation BIGINT,
    vesting_months      SMALLINT DEFAULT 12,
    snapshot_at         TIMESTAMPTZ,
    distribution_starts TIMESTAMPTZ,
    distribution_ends   TIMESTAMPTZ,
    eligibility_rules   JSONB   DEFAULT '{}',
    -- e.g.: {"min_xp":500,"min_missions":10,"communities":["uuid1"]}
    status              TEXT    DEFAULT 'draft'
                        CHECK (status IN ('draft','snapshot','distributing','completed')),
    created_at          TIMESTAMPTZ DEFAULT NOW()
);


CREATE TABLE public.airdrop_allocations (
    id                  UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id         UUID    NOT NULL REFERENCES public.airdrop_campaigns(id),
    user_id             UUID    NOT NULL REFERENCES public.users(id),
    wallet_address      TEXT    NOT NULL,
    allocation_amount   BIGINT  NOT NULL,
    vested_amount       BIGINT  DEFAULT 0,
    claimed_amount      BIGINT  DEFAULT 0,
    last_vest_at        TIMESTAMPTZ,
    fully_vested_at     TIMESTAMPTZ,
    claim_tx            TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (campaign_id, user_id)
);

CREATE INDEX idx_airdrop_alloc_campaign ON public.airdrop_allocations(campaign_id);
CREATE INDEX idx_airdrop_alloc_user     ON public.airdrop_allocations(user_id);


-- ============================================================
-- DOMAIN 9 — TRUST & ANTI-SYBIL
-- ============================================================

CREATE TABLE public.trust_events (
    id          UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID    NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    event_type  TEXT    NOT NULL,
    -- 'stake_verified' | 'x_oauth_verified' | 'wallet_verified' |
    -- 'burst_activity_detected' | 'multi_wallet_detected' |
    -- 'offerwall_fraud' | 'manual_review_passed' | 'manual_review_failed'
    delta       SMALLINT NOT NULL,
    score_after SMALLINT NOT NULL,
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_trust_event_user ON public.trust_events(user_id);
CREATE INDEX idx_trust_event_time ON public.trust_events(created_at DESC);


CREATE TABLE public.sybil_flags (
    id          UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID    NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    flag_type   TEXT    NOT NULL,
    -- 'multi_wallet' | 'ip_cluster' | 'burst_pattern' |
    -- 'offerwall_abuse' | 'referral_ring' | 'bot_behavior'
    severity    TEXT    DEFAULT 'low'
                CHECK (severity IN ('low','medium','high','critical')),
    evidence    JSONB   DEFAULT '{}',
    resolved    BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMPTZ,
    resolved_by TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_sybil_user     ON public.sybil_flags(user_id);
CREATE INDEX idx_sybil_open     ON public.sybil_flags(created_at DESC)
    WHERE resolved = FALSE;


-- ============================================================
-- DOMAIN 10 — ANALYTICS (append-only, high-volume)
-- ============================================================

CREATE TABLE analytics.daily_community_stats (
    id                      UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    community_id            UUID    NOT NULL REFERENCES public.communities(id) ON DELETE CASCADE,
    date                    DATE    NOT NULL,
    new_members             INTEGER DEFAULT 0,
    active_members          INTEGER DEFAULT 0,
    missions_completed      INTEGER DEFAULT 0,
    xp_distributed          INTEGER DEFAULT 0,
    fee_received_sol        BIGINT  DEFAULT 0,
    prize_pool_balance      BIGINT  DEFAULT 0,
    competition_entries     INTEGER DEFAULT 0,
    offerwall_completions   INTEGER DEFAULT 0,
    offerwall_revenue_usd   NUMERIC(10,4) DEFAULT 0,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (community_id, date)
);

CREATE INDEX idx_daily_stats ON analytics.daily_community_stats(community_id, date DESC);


CREATE TABLE analytics.xp_events (
    id              BIGSERIAL   PRIMARY KEY,
    user_id         UUID        NOT NULL,
    community_id    UUID        NOT NULL,
    mission_id      UUID,
    xp_amount       INTEGER     NOT NULL,
    multiplier      NUMERIC(4,2) DEFAULT 1.00,
    source          TEXT        NOT NULL,
    -- 'mission' | 'streak_bonus' | 'referral' | 'boost' | 'competition'
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_xp_events_user      ON analytics.xp_events(user_id,      created_at DESC);
CREATE INDEX idx_xp_events_community ON analytics.xp_events(community_id, created_at DESC);


-- ============================================================
-- FUNCTIONS
-- ============================================================

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION public.touch_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

-- Apply touch_updated_at to all relevant tables
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'users','creators','communities','community_members',
        'mission_templates','mission_completions',
        'competitions','competition_entries',
        'fee_routing_configs','user_login_streaks'
    ] LOOP
        EXECUTE format(
            'CREATE TRIGGER trg_%s_updated_at
             BEFORE UPDATE ON public.%I
             FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at()', t, t
        );
    END LOOP;
END $$;

CREATE TRIGGER trg_agent_wallet_updated_at
    BEFORE UPDATE ON agent.wallet_state
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();


-- ─── award_xp() ─────────────────────────────────────────────────────────────
-- Atomically awards XP, recalculates level, updates all counters.
-- Call this after a mission_completion is marked 'verified'.
CREATE OR REPLACE FUNCTION public.award_xp(
    p_user_id       UUID,
    p_community_id  UUID,
    p_mission_id    UUID,
    p_completion_id UUID,
    p_xp_base       INTEGER,
    p_multiplier    NUMERIC  DEFAULT 1.00,
    p_source        TEXT     DEFAULT 'mission'
)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    v_xp_final     INTEGER;
    v_new_xp_total INTEGER;
    v_new_level    user_level;
BEGIN
    v_xp_final := GREATEST(1, FLOOR(p_xp_base * p_multiplier));

    -- Award to member
    UPDATE public.community_members
    SET
        xp_total           = xp_total + v_xp_final,
        xp_this_week       = xp_this_week + v_xp_final,
        xp_this_month      = xp_this_month + v_xp_final,
        missions_completed = missions_completed + 1,
        last_mission_at    = NOW()
    WHERE user_id = p_user_id AND community_id = p_community_id
    RETURNING xp_total INTO v_new_xp_total;

    -- Recalculate level
    -- Bronze 0-999 | Silver 1000-4999 | Gold 5000-19999 | Elite 20000+
    v_new_level := CASE
        WHEN v_new_xp_total >= 20000 THEN 'elite'::user_level
        WHEN v_new_xp_total >=  5000 THEN 'gold'::user_level
        WHEN v_new_xp_total >=  1000 THEN 'silver'::user_level
        ELSE 'bronze'::user_level
    END;

    UPDATE public.community_members
    SET level = v_new_level
    WHERE user_id = p_user_id AND community_id = p_community_id;

    -- Update mission template stats
    UPDATE public.mission_templates
    SET total_completions = total_completions + 1,
        total_xp_awarded  = total_xp_awarded  + v_xp_final
    WHERE id = p_mission_id;

    -- Stamp completion
    UPDATE public.mission_completions
    SET xp_awarded              = v_xp_final,
        xp_multiplier_applied   = p_multiplier
    WHERE id = p_completion_id;

    -- Analytics
    INSERT INTO analytics.xp_events(user_id, community_id, mission_id, xp_amount, multiplier, source)
    VALUES (p_user_id, p_community_id, p_mission_id, v_xp_final, p_multiplier, p_source);

    -- Community counters
    UPDATE public.communities
    SET total_xp_distributed     = total_xp_distributed     + v_xp_final,
        total_missions_completed = total_missions_completed + 1
    WHERE id = p_community_id;

    RETURN v_xp_final;
END;
$$;


-- ─── claim_streak() ──────────────────────────────────────────────────────────
-- Processes a daily streak check-in. Returns XP awarded.
-- Raises exception 'streak_already_claimed' if user already checked in today.
CREATE OR REPLACE FUNCTION public.claim_streak(
    p_user_id      UUID,
    p_community_id UUID
)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    v_streak    RECORD;
    v_today     DATE    := CURRENT_DATE;
    v_xp_award  INTEGER;
    v_new_streak INTEGER;
BEGIN
    SELECT * INTO v_streak
    FROM public.user_login_streaks
    WHERE user_id = p_user_id AND community_id = p_community_id
    FOR UPDATE;

    IF NOT FOUND THEN
        INSERT INTO public.user_login_streaks(user_id, community_id)
        VALUES (p_user_id, p_community_id);
        SELECT * INTO v_streak
        FROM public.user_login_streaks
        WHERE user_id = p_user_id AND community_id = p_community_id;
    END IF;

    IF v_streak.last_checkin_date = v_today THEN
        RAISE EXCEPTION 'streak_already_claimed';
    END IF;

    -- Streak continues if last check-in was yesterday, otherwise reset
    IF v_streak.last_checkin_date = v_today - INTERVAL '1 day' THEN
        v_new_streak := v_streak.current_streak + 1;
    ELSE
        v_new_streak := 1;
    END IF;

    -- XP scale: D1=10 D2=15 D3=20 D4=25 D5=30 D6=40 D7+=60
    v_xp_award := CASE
        WHEN v_new_streak = 1 THEN 10
        WHEN v_new_streak = 2 THEN 15
        WHEN v_new_streak = 3 THEN 20
        WHEN v_new_streak = 4 THEN 25
        WHEN v_new_streak = 5 THEN 30
        WHEN v_new_streak = 6 THEN 40
        ELSE 60
    END;

    UPDATE public.user_login_streaks SET
        current_streak    = v_new_streak,
        longest_streak    = GREATEST(longest_streak, v_new_streak),
        last_checkin_date = v_today,
        total_checkins    = total_checkins + 1,
        xp_earned_total   = xp_earned_total + v_xp_award
    WHERE user_id = p_user_id AND community_id = p_community_id;

    UPDATE public.community_members SET
        xp_total      = xp_total      + v_xp_award,
        xp_this_week  = xp_this_week  + v_xp_award,
        xp_this_month = xp_this_month + v_xp_award
    WHERE user_id = p_user_id AND community_id = p_community_id;

    INSERT INTO analytics.xp_events(user_id, community_id, xp_amount, source)
    VALUES (p_user_id, p_community_id, v_xp_award, 'streak_bonus');

    RETURN v_xp_award;
END;
$$;


-- ─── sync_member_count() — trigger ──────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.sync_member_count()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE public.communities
        SET member_count = member_count + 1
        WHERE id = NEW.community_id;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE public.communities
        SET member_count = GREATEST(0, member_count - 1)
        WHERE id = OLD.community_id;
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_member_count
    AFTER INSERT OR DELETE ON public.community_members
    FOR EACH ROW EXECUTE FUNCTION public.sync_member_count();


-- ─── reset_daily_missions() — run by cron at 00:00 UTC ───────────────────────
CREATE OR REPLACE FUNCTION public.reset_daily_missions()
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    UPDATE public.community_members
    SET missions_today = 0, missions_today_date = CURRENT_DATE
    WHERE missions_today_date < CURRENT_DATE;
END;
$$;

-- ─── reset_weekly_xp() — run by cron each Monday 00:00 UTC ──────────────────
CREATE OR REPLACE FUNCTION public.reset_weekly_xp()
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    UPDATE public.community_members
    SET xp_this_week = 0, xp_week_reset_at = NOW()
    WHERE xp_week_reset_at < date_trunc('week', NOW());
END;
$$;


-- ============================================================
-- VIEWS
-- ============================================================

-- Leaderboard with live ranks
CREATE OR REPLACE VIEW public.v_leaderboard AS
SELECT
    cm.community_id,
    cm.user_id,
    u.x_handle,
    u.x_display_name,
    u.x_avatar_url,
    cm.level,
    cm.xp_total,
    cm.xp_this_week,
    cm.missions_completed,
    cm.is_staked,
    RANK() OVER (PARTITION BY cm.community_id ORDER BY cm.xp_total      DESC) AS rank_all_time,
    RANK() OVER (PARTITION BY cm.community_id ORDER BY cm.xp_this_week  DESC) AS rank_weekly
FROM public.community_members cm
JOIN public.users u ON cm.user_id = u.id
WHERE u.is_banned = FALSE AND u.deleted_at IS NULL;


-- Active competitions with derived pool total and countdown
CREATE OR REPLACE VIEW public.v_active_competitions AS
SELECT
    c.*,
    c.prize_pool_sol + c.fee_accumulated_sol   AS total_pool_sol,
    EXTRACT(EPOCH FROM (c.ends_at - NOW()))::BIGINT AS seconds_remaining,
    (SELECT COUNT(*) FROM public.competition_entries e WHERE e.competition_id = c.id)
        AS participant_count
FROM public.competitions c
WHERE c.status = 'active' AND c.ends_at > NOW();


-- Explore page community cards
CREATE OR REPLACE VIEW public.v_community_explore AS
SELECT
    c.id, c.name, c.slug, c.description,
    c.avatar_url, c.cover_url, c.accent_color,
    c.token_address, c.token_symbol, c.launch_platform,
    c.member_count, c.total_xp_distributed,
    c.x_community_url, c.website_url, c.pump_fun_url,
    COALESCE(
        (SELECT SUM(ac.prize_pool_sol + ac.fee_accumulated_sol)
         FROM public.competitions ac
         WHERE ac.community_id = c.id AND ac.status = 'active'),
        0
    ) AS active_prize_pool_sol,
    (SELECT COUNT(*) FROM public.competitions ac
     WHERE ac.community_id = c.id AND ac.status = 'active') AS active_competitions
FROM public.communities c
WHERE c.is_active = TRUE AND c.deleted_at IS NULL;


-- Full user dashboard state (one row per user per community)
CREATE OR REPLACE VIEW public.v_user_dashboard AS
SELECT
    u.id              AS user_id,
    u.x_handle,
    u.x_display_name,
    u.x_avatar_url,
    u.wallet_address,
    u.trust_score,
    cm.community_id,
    cm.level,
    cm.xp_total,
    cm.xp_this_week,
    cm.missions_completed,
    cm.missions_today,
    cm.is_staked,
    cm.xp_multiplier,
    ls.current_streak,
    ls.longest_streak,
    ls.last_checkin_date,
    RANK() OVER (PARTITION BY cm.community_id ORDER BY cm.xp_total     DESC) AS rank_all_time,
    RANK() OVER (PARTITION BY cm.community_id ORDER BY cm.xp_this_week DESC) AS rank_weekly
FROM public.users u
JOIN public.community_members cm ON u.id = cm.user_id
LEFT JOIN public.user_login_streaks ls
    ON u.id = ls.user_id AND cm.community_id = ls.community_id
WHERE u.deleted_at IS NULL;


-- ============================================================
-- ROW LEVEL SECURITY
-- ============================================================

ALTER TABLE public.users                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.creators              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.communities           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.community_members     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.mission_templates     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.mission_completions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.competitions          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.competition_entries   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_login_streaks    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.offerwall_completions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.token_transactions    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trust_events          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sybil_flags           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.airdrop_allocations   ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent.actions                ENABLE ROW LEVEL SECURITY;

-- Users: own row only
CREATE POLICY pol_users_self_read  ON public.users FOR SELECT USING (auth.uid() = auth_id);
CREATE POLICY pol_users_self_write ON public.users FOR UPDATE USING (auth.uid() = auth_id);

-- Communities: public read if active; creator writes own
CREATE POLICY pol_communities_read ON public.communities
    FOR SELECT USING (is_active = TRUE AND deleted_at IS NULL);

CREATE POLICY pol_communities_creator_write ON public.communities
    FOR ALL USING (
        creator_id IN (
            SELECT cr.id FROM public.creators cr
            JOIN public.users u ON cr.user_id = u.id
            WHERE u.auth_id = auth.uid()
        )
    );

-- Members: own row + public leaderboard read
CREATE POLICY pol_members_self ON public.community_members
    FOR ALL USING (user_id = (SELECT id FROM public.users WHERE auth_id = auth.uid()));

CREATE POLICY pol_members_leaderboard ON public.community_members
    FOR SELECT USING (TRUE);

-- Mission templates: public read in active communities
CREATE POLICY pol_missions_read ON public.mission_templates
    FOR SELECT USING (
        status = 'active' AND
        community_id IN (SELECT id FROM public.communities WHERE is_active = TRUE)
    );

CREATE POLICY pol_missions_creator_write ON public.mission_templates
    FOR ALL USING (
        community_id IN (
            SELECT c.id FROM public.communities c
            JOIN public.creators cr ON c.creator_id = cr.id
            JOIN public.users u ON cr.user_id = u.id
            WHERE u.auth_id = auth.uid()
        )
    );

-- Completions: own row only
CREATE POLICY pol_completions_self ON public.mission_completions
    FOR SELECT USING (user_id = (SELECT id FROM public.users WHERE auth_id = auth.uid()));

CREATE POLICY pol_completions_insert ON public.mission_completions
    FOR INSERT WITH CHECK (user_id = (SELECT id FROM public.users WHERE auth_id = auth.uid()));

-- Competitions: public read (not draft); creator writes
CREATE POLICY pol_competitions_read ON public.competitions
    FOR SELECT USING (status != 'draft');

CREATE POLICY pol_competitions_creator_write ON public.competitions
    FOR ALL USING (
        community_id IN (
            SELECT c.id FROM public.communities c
            JOIN public.creators cr ON c.creator_id = cr.id
            JOIN public.users u ON cr.user_id = u.id
            WHERE u.auth_id = auth.uid()
        )
    );

-- Competition entries: public leaderboard read, self insert
CREATE POLICY pol_entries_read ON public.competition_entries FOR SELECT USING (TRUE);
CREATE POLICY pol_entries_insert ON public.competition_entries
    FOR INSERT WITH CHECK (user_id = (SELECT id FROM public.users WHERE auth_id = auth.uid()));

-- Agent actions: public read (transparency contract)
CREATE POLICY pol_agent_read ON agent.actions FOR SELECT USING (TRUE);

-- Airdrop allocations: own row
CREATE POLICY pol_airdrop_self ON public.airdrop_allocations
    FOR SELECT USING (user_id = (SELECT id FROM public.users WHERE auth_id = auth.uid()));

-- Trust events: own row
CREATE POLICY pol_trust_self ON public.trust_events
    FOR SELECT USING (user_id = (SELECT id FROM public.users WHERE auth_id = auth.uid()));

-- Sybil flags: service_role only (no user policy)


-- ============================================================
-- SEED DATA
-- ============================================================

INSERT INTO public.offerwall_providers (name, is_active, config) VALUES
    ('offertoro', TRUE, '{"postback_param":"transaction_id","base_url":"https://api.offertoro.com"}'),
    ('adgate',    TRUE, '{"postback_param":"tid","base_url":"https://adgaterewards.com"}'),
    ('freecash',  TRUE, '{"postback_param":"ref_id","base_url":"https://freecash.com"}')
ON CONFLICT (name) DO NOTHING;

INSERT INTO agent.wallet_state (wallet_address, network)
VALUES ('PLACEHOLDER_REPLACE_WITH_REAL_WALLET', 'mainnet-beta')
ON CONFLICT (wallet_address) DO NOTHING;


-- ============================================================
-- COMMENTS
-- ============================================================

COMMENT ON TABLE  public.users               IS 'Core user identity. auth_id links to Supabase Auth.';
COMMENT ON TABLE  public.creators            IS 'Users who own communities. Manages plan and revenue.';
COMMENT ON TABLE  public.communities         IS 'Multi-tenant core. Isolated gamification environment per creator.';
COMMENT ON TABLE  public.community_members   IS 'All per-user-per-community state: XP, level, streak, stake.';
COMMENT ON TABLE  public.mission_templates   IS 'Creator-defined missions. verification_data drives verification logic.';
COMMENT ON TABLE  public.mission_completions IS 'Every attempt. Pending until verified by appropriate method.';
COMMENT ON TABLE  public.competitions        IS 'Time-limited prize events. Supports 3 prize stack models.';
COMMENT ON TABLE  public.fee_routing_events  IS 'On-chain fee payments from pump.fun/bags.fm. Source of truth for prize pool.';
COMMENT ON TABLE  agent.actions              IS 'Full audit log of every AI agent action. Public-readable for transparency.';
COMMENT ON FUNCTION public.award_xp          IS 'Atomic XP award + level recalc + analytics. Call after verification.';
COMMENT ON FUNCTION public.claim_streak      IS 'Daily streak check-in. Raises streak_already_claimed if duplicate.';
COMMENT ON FUNCTION public.reset_daily_missions IS 'Cron: run at 00:00 UTC daily to reset missions_today.';
COMMENT ON FUNCTION public.reset_weekly_xp   IS 'Cron: run every Monday 00:00 UTC to reset xp_this_week.';

-- ── END OF SCHEMA ─────────────────────────────────────────────────────────────
