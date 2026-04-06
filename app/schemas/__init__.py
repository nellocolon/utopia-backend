from __future__ import annotations
from pydantic import BaseModel, Field
from datetime import datetime, date
from uuid import UUID
from typing import Optional, List
from enum import Enum


class UserLevel(str, Enum):
    bronze = "bronze"
    silver = "silver"
    gold   = "gold"
    elite  = "elite"

class MissionType(str, Enum):
    onboarding = "onboarding"
    daily      = "daily"
    clipping   = "clipping"
    special    = "special"
    referral   = "referral"

class CompletionStatus(str, Enum):
    pending  = "pending"
    verified = "verified"
    rejected = "rejected"
    expired  = "expired"
    revoked  = "revoked"

class PrizeStackModel(str, Enum):
    fixed           = "fixed"
    fixed_plus_fees = "fixed_plus_fees"
    fees_only       = "fees_only"

class CompetitionStatus(str, Enum):
    draft       = "draft"
    active      = "active"
    ended       = "ended"
    distributed = "distributed"
    cancelled   = "cancelled"


# Auth
class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: UUID
    x_handle: str | None = None
    needs_wallet: bool = False

class WalletConnectRequest(BaseModel):
    wallet_address: str = Field(..., min_length=32, max_length=44)
    signature: str
    message: str

class UserProfile(BaseModel):
    id: UUID
    x_handle: str | None
    x_display_name: str | None
    x_avatar_url: str | None
    wallet_address: str | None
    trust_score: int
    x_followers_count: int = 0
    is_banned: bool = False
    last_active_at: datetime
    created_at: datetime


# Dashboard
class MemberDashboard(BaseModel):
    user_id: UUID
    community_id: UUID
    x_handle: str | None
    x_display_name: str | None
    x_avatar_url: str | None
    wallet_address: str | None
    trust_score: int
    level: UserLevel
    xp_total: int
    xp_this_week: int
    xp_multiplier: float
    xp_multiplier_expires_at: datetime | None
    missions_completed: int
    missions_today: int
    is_staked: bool
    stake_amount: int
    current_streak: int
    longest_streak: int
    last_checkin_date: date | None
    rank_all_time: int | None
    rank_weekly: int | None


# Communities
class CommunityCard(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str | None
    avatar_url: str | None
    cover_url: str | None
    accent_color: str
    token_address: str | None
    token_symbol: str | None
    launch_platform: str | None
    member_count: int
    active_prize_pool_sol: int
    active_competitions: int

class CommunityDetail(CommunityCard):
    creator_id: UUID
    x_community_url: str | None
    website_url: str | None
    pump_fun_url: str | None
    telegram_url: str | None
    total_xp_distributed: int
    total_missions_completed: int
    fee_routing_enabled: bool
    created_at: datetime

class CommunityCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=64)
    slug: str = Field(..., min_length=2, max_length=40, pattern=r'^[a-z0-9-]+$')
    description: str | None = Field(None, max_length=500)
    accent_color: str = Field("#E1FF00", pattern=r'^#[0-9A-Fa-f]{6}$')
    token_address: str | None = None
    token_symbol: str | None = None
    launch_platform: str | None = None
    x_community_url: str | None = None
    website_url: str | None = None
    pump_fun_url: str | None = None


# Missions
class MissionTemplate(BaseModel):
    id: UUID
    community_id: UUID
    title: str
    description: str | None
    type: MissionType
    xp_reward: int
    verification_method: str
    is_daily: bool
    icon: str
    sort_order: int
    offerwall_provider: str | None
    affiliate_url: str | None
    total_completions: int

class MissionWithStatus(MissionTemplate):
    user_completed_today: bool
    user_completion_status: CompletionStatus | None

class MissionSubmitRequest(BaseModel):
    mission_id: UUID
    community_id: UUID
    proof_data: dict = Field(default_factory=dict)

class MissionSubmitResponse(BaseModel):
    completion_id: UUID
    status: CompletionStatus
    xp_awarded: int
    message: str


# Leaderboard
class LeaderboardEntry(BaseModel):
    rank: int
    user_id: UUID
    x_handle: str | None
    x_display_name: str | None
    x_avatar_url: str | None
    level: UserLevel
    xp_total: int
    xp_this_week: int
    missions_completed: int
    is_staked: bool
    is_me: bool = False

class LeaderboardResponse(BaseModel):
    community_id: UUID
    period: str
    total_members: int
    entries: List[LeaderboardEntry]
    my_rank: int | None


# Competitions
class PrizeTier(BaseModel):
    rank: int
    pct_of_pool: float
    label: str | None

class CompetitionPublic(BaseModel):
    id: UUID
    community_id: UUID
    title: str
    description: str | None
    prize_stack_model: PrizeStackModel
    status: CompetitionStatus
    total_pool_sol: int
    prize_currency: str
    requires_stake: bool
    starts_at: datetime
    ends_at: datetime
    seconds_remaining: int
    participant_count: int
    prize_tiers: List[PrizeTier] = []

class CompetitionEnterResponse(BaseModel):
    entry_id: UUID
    message: str


# Streak
class StreakClaimResponse(BaseModel):
    xp_awarded: int
    new_streak: int
    message: str


# Fee routing
class FeeRoutingSetupResponse(BaseModel):
    community_id: UUID
    escrow_wallet: str
    agent_wallet: str
    suggested_splits: dict
    instructions: str


# Generic
class MessageResponse(BaseModel):
    message: str

class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
