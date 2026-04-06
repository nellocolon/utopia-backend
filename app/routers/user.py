from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from uuid import UUID

from app.database import get_db
from app.middleware.auth import get_current_user
from app.schemas import MemberDashboard, StreakClaimResponse, MessageResponse
import structlog

router = APIRouter(prefix="/me", tags=["user"])
logger = structlog.get_logger()

MIN_STAKE_LAMPORTS = 5_000_000


@router.get("/dashboard/{community_id}", response_model=MemberDashboard)
async def get_dashboard(
    community_id: UUID,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
):
    row = await db.fetchrow(
        "SELECT * FROM public.v_user_dashboard WHERE user_id=$1 AND community_id=$2",
        user_id, community_id)
    if not row:
        raise HTTPException(404, "Not a member of this community")
    return dict(row)


@router.post("/streak/{community_id}", response_model=StreakClaimResponse)
async def claim_streak(
    community_id: UUID,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
):
    try:
        xp = await db.fetchval("SELECT public.claim_streak($1,$2)", user_id, community_id)
    except Exception as e:
        if "streak_already_claimed" in str(e):
            raise HTTPException(409, "Streak already claimed today. Come back tomorrow.")
        raise HTTPException(400, f"Streak claim failed: {str(e)}")

    streak_row = await db.fetchrow(
        "SELECT current_streak FROM public.user_login_streaks WHERE user_id=$1 AND community_id=$2",
        user_id, community_id)
    streak = streak_row["current_streak"] if streak_row else 1

    if streak >= 7:
        msg = f"🔥 {streak}-day streak! Max bonus — +{xp} XP"
    elif streak >= 3:
        msg = f"⚡ {streak}-day streak — +{xp} XP"
    else:
        msg = f"Day {streak} — +{xp} XP"

    return StreakClaimResponse(xp_awarded=xp, new_streak=streak, message=msg)


@router.post("/stake/{community_id}", response_model=MessageResponse)
async def stake_for_community(
    community_id: UUID,
    tx_signature: str,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
):
    member = await db.fetchrow(
        "SELECT id, is_staked FROM public.community_members WHERE community_id=$1 AND user_id=$2",
        community_id, user_id)
    if not member:
        raise HTTPException(404, "Not a member of this community")
    if member["is_staked"]:
        return MessageResponse(message="Already staked for this community")

    await db.execute("""
        UPDATE public.community_members
        SET is_staked=TRUE, stake_amount=$3, staked_at=NOW()
        WHERE community_id=$1 AND user_id=$2
    """, community_id, user_id, MIN_STAKE_LAMPORTS)

    user_row = await db.fetchrow("SELECT trust_score FROM public.users WHERE id=$1", user_id)
    new_score = min(100, (user_row["trust_score"] or 50) + 10)

    await db.execute("""
        INSERT INTO public.trust_events (user_id, event_type, delta, score_after, notes)
        VALUES ($1,'stake_verified',10,$2,$3)
    """, user_id, new_score, f"Staked for community {community_id}. Tx: {tx_signature}")

    await db.execute("UPDATE public.users SET trust_score=$2 WHERE id=$1", user_id, new_score)

    await db.execute("""
        INSERT INTO public.token_transactions
            (community_id, user_id, type, amount_lamports, direction, tx_signature, notes)
        VALUES ($1,$2,'stake_lock',$3,'+', $4,'Competition stake')
    """, community_id, user_id, MIN_STAKE_LAMPORTS, tx_signature)

    return MessageResponse(message="Staked. You can now enter competitions.")


@router.delete("/stake/{community_id}", response_model=MessageResponse)
async def unstake(
    community_id: UUID,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
):
    await db.execute("""
        UPDATE public.community_members SET is_staked=FALSE, stake_amount=0
        WHERE community_id=$1 AND user_id=$2
    """, community_id, user_id)
    return MessageResponse(message="Unstaked.")
