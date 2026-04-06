from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from uuid import UUID
from typing import Literal

from app.database import get_db
from app.middleware.auth import get_current_user, get_optional_user
from app.schemas import (
    CommunityCard, CommunityDetail, CommunityCreateRequest,
    LeaderboardResponse, LeaderboardEntry, MessageResponse, UserLevel
)
import structlog

router = APIRouter(prefix="/communities", tags=["communities"])
logger = structlog.get_logger()


@router.get("", response_model=list[CommunityCard])
async def explore_communities(
    search:        str | None = Query(None),
    token_address: str | None = Query(None),
    limit:  int = Query(20, ge=1, le=100),
    offset: int = Query(0,  ge=0),
    db = Depends(get_db),
):
    query  = """
        SELECT c.id, c.name, c.slug, c.description,
               c.avatar_url, c.cover_url, c.accent_color,
               c.token_address, c.token_symbol, c.launch_platform, c.member_count,
               COALESCE((SELECT SUM(comp.prize_pool_sol + comp.fee_accumulated_sol)
                         FROM public.competitions comp
                         WHERE comp.community_id = c.id AND comp.status = 'active'),0)::BIGINT AS active_prize_pool_sol,
               (SELECT COUNT(*) FROM public.competitions comp
                WHERE comp.community_id = c.id AND comp.status = 'active')::INT AS active_competitions
        FROM public.communities c
        WHERE c.is_active = TRUE AND c.deleted_at IS NULL
    """
    params: list = []
    if search:
        params.append(f"%{search}%")
        query += f" AND c.name ILIKE ${len(params)}"
    if token_address:
        params.append(f"%{token_address}%")
        query += f" AND c.token_address ILIKE ${len(params)}"
    query += f" ORDER BY c.member_count DESC LIMIT ${len(params)+1} OFFSET ${len(params)+2}"
    params += [limit, offset]
    rows = await db.fetch(query, *params)
    return [dict(r) for r in rows]


@router.get("/{slug}", response_model=CommunityDetail)
async def get_community(slug: str, db = Depends(get_db)):
    row = await db.fetchrow("""
        SELECT c.*, cr.id AS creator_id
        FROM public.communities c
        JOIN public.creators cr ON c.creator_id = cr.id
        WHERE c.slug = $1 AND c.is_active = TRUE AND c.deleted_at IS NULL
    """, slug)
    if not row:
        raise HTTPException(404, "Community not found")
    return dict(row)


@router.post("", response_model=CommunityDetail, status_code=201)
async def create_community(
    body: CommunityCreateRequest,
    user_id: UUID = Depends(get_current_user),
    db = Depends(get_db),
):
    creator = await db.fetchrow("""
        SELECT cr.id, cr.plan FROM public.creators cr
        JOIN public.users u ON cr.user_id = u.id WHERE u.id = $1
    """, user_id)
    if not creator:
        raise HTTPException(403, "Register as a creator first")
    if creator["plan"] != "premium":
        raise HTTPException(403, "Premium plan required")
    if await db.fetchval("SELECT 1 FROM public.communities WHERE slug = $1", body.slug):
        raise HTTPException(409, "Slug already taken")
    row = await db.fetchrow("""
        INSERT INTO public.communities
            (creator_id, name, slug, description, accent_color,
             token_address, token_symbol, launch_platform,
             x_community_url, website_url, pump_fun_url)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING *
    """, creator["id"], body.name, body.slug, body.description, body.accent_color,
         body.token_address, body.token_symbol, body.launch_platform,
         body.x_community_url, body.website_url, body.pump_fun_url)
    return dict(row)


@router.post("/{community_id}/join", response_model=MessageResponse)
async def join_community(
    community_id: UUID,
    user_id: UUID = Depends(get_current_user),
    db = Depends(get_db),
):
    if not await db.fetchrow("SELECT id FROM public.communities WHERE id=$1 AND is_active=TRUE", community_id):
        raise HTTPException(404, "Community not found")
    await db.execute("""
        INSERT INTO public.community_members (community_id, user_id)
        VALUES ($1,$2) ON CONFLICT (community_id, user_id) DO NOTHING
    """, community_id, user_id)
    await db.execute("""
        INSERT INTO public.user_login_streaks (user_id, community_id)
        VALUES ($1,$2) ON CONFLICT (user_id, community_id) DO NOTHING
    """, user_id, community_id)
    return MessageResponse(message="Joined community successfully")


@router.get("/{community_id}/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard(
    community_id: UUID,
    period: Literal["all_time","weekly"] = Query("all_time"),
    limit:  int = Query(50, ge=1, le=200),
    user_id: UUID | None = Depends(get_optional_user),
    db = Depends(get_db),
):
    rank_col = "rank_all_time" if period == "all_time" else "rank_weekly"
    total    = await db.fetchval(
        "SELECT COUNT(*) FROM public.community_members WHERE community_id=$1", community_id)
    rows = await db.fetch(f"""
        SELECT {rank_col} AS rank, user_id, x_handle, x_display_name, x_avatar_url,
               level, xp_total, xp_this_week, missions_completed, is_staked
        FROM public.v_leaderboard WHERE community_id=$1 ORDER BY rank ASC LIMIT $2
    """, community_id, limit)

    entries  = []
    my_rank  = None
    for r in rows:
        entry = LeaderboardEntry(
            rank=r["rank"], user_id=r["user_id"],
            x_handle=r["x_handle"], x_display_name=r["x_display_name"],
            x_avatar_url=r["x_avatar_url"], level=r["level"],
            xp_total=r["xp_total"], xp_this_week=r["xp_this_week"],
            missions_completed=r["missions_completed"], is_staked=r["is_staked"],
            is_me=(user_id is not None and r["user_id"] == user_id),
        )
        entries.append(entry)
        if user_id and r["user_id"] == user_id:
            my_rank = r["rank"]

    if user_id and my_rank is None:
        my_rank = await db.fetchval(
            f"SELECT {rank_col} FROM public.v_leaderboard WHERE community_id=$1 AND user_id=$2",
            community_id, user_id)

    return LeaderboardResponse(community_id=community_id, period=period,
                                total_members=total, entries=entries, my_rank=my_rank)
