from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from uuid import UUID
import datetime as dt

from app.database import get_db
from app.middleware.auth import get_current_user, get_optional_user
from app.schemas import CompetitionPublic, CompetitionEnterResponse, PrizeTier
import structlog

router = APIRouter(prefix="/competitions", tags=["competitions"])
logger = structlog.get_logger()


@router.get("/{community_id}", response_model=list[CompetitionPublic])
async def get_active_competitions(community_id: UUID, db=Depends(get_db)):
    rows = await db.fetch("""
        SELECT c.*,
               c.prize_pool_sol + c.fee_accumulated_sol AS total_pool_sol,
               GREATEST(0, EXTRACT(EPOCH FROM (c.ends_at - NOW()))::BIGINT) AS seconds_remaining,
               (SELECT COUNT(*) FROM public.competition_entries e
                WHERE e.competition_id = c.id) AS participant_count
        FROM public.competitions c
        WHERE c.community_id=$1 AND c.status IN ('active','draft')
          AND c.ends_at > NOW() - INTERVAL '1 hour'
        ORDER BY c.starts_at ASC
    """, community_id)

    results = []
    for r in rows:
        tiers = await db.fetch("""
            SELECT rank, pct_of_pool, label FROM public.competition_prize_tiers
            WHERE competition_id=$1 ORDER BY rank ASC
        """, r["id"])
        comp = dict(r)
        comp["prize_tiers"] = [dict(t) for t in tiers]
        results.append(comp)
    return results


@router.post("/{competition_id}/enter", response_model=CompetitionEnterResponse)
async def enter_competition(
    competition_id: UUID,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
):
    comp = await db.fetchrow(
        "SELECT id, community_id, status, requires_stake, starts_at, ends_at "
        "FROM public.competitions WHERE id=$1", competition_id)
    if not comp:
        raise HTTPException(404, "Competition not found")
    if comp["status"] != "active":
        raise HTTPException(400, f"Competition not active (status: {comp['status']})")

    now = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    if now < comp["starts_at"]:
        raise HTTPException(400, "Competition has not started yet")
    if now > comp["ends_at"]:
        raise HTTPException(400, "Competition has ended")

    member = await db.fetchrow("""
        SELECT is_staked FROM public.community_members
        WHERE community_id=$1 AND user_id=$2
    """, comp["community_id"], user_id)
    if not member:
        raise HTTPException(403, "Join the community first")
    if comp["requires_stake"] and not member["is_staked"]:
        raise HTTPException(403, "Stake minimum required to enter competitions")

    entry_id = await db.fetchval("""
        INSERT INTO public.competition_entries (competition_id, user_id, community_id)
        VALUES ($1,$2,$3)
        ON CONFLICT (competition_id, user_id) DO UPDATE SET updated_at=NOW()
        RETURNING id
    """, competition_id, user_id, comp["community_id"])

    return CompetitionEnterResponse(
        entry_id=entry_id,
        message="Entered. Complete missions to earn XP and climb the leaderboard.")


@router.get("/{competition_id}/leaderboard")
async def get_competition_leaderboard(
    competition_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    db=Depends(get_db),
):
    rows = await db.fetch("""
        SELECT ce.competition_id, ce.user_id, u.x_handle,
               ce.xp_competition,
               RANK() OVER (ORDER BY ce.xp_competition DESC) AS rank_current,
               ce.prize_tier, ce.prize_amount_sol, ce.prize_paid
        FROM public.competition_entries ce
        JOIN public.users u ON ce.user_id = u.id
        WHERE ce.competition_id=$1
        ORDER BY ce.xp_competition DESC LIMIT $2
    """, competition_id, limit)
    return [dict(r) for r in rows]
