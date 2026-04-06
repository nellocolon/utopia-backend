from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from uuid import UUID
from datetime import date

from app.database import get_db
from app.middleware.auth import get_current_user
from app.schemas import MissionWithStatus, MissionSubmitRequest, MissionSubmitResponse, CompletionStatus
import structlog

router = APIRouter(prefix="/missions", tags=["missions"])
logger = structlog.get_logger()


@router.get("/{community_id}", response_model=list[MissionWithStatus])
async def get_missions(
    community_id: UUID,
    user_id: UUID = Depends(get_current_user),
    db = Depends(get_db),
):
    rows = await db.fetch("""
        SELECT mt.*,
               CASE WHEN mc.id IS NOT NULL THEN TRUE ELSE FALSE END AS user_completed_today,
               mc.status AS user_completion_status
        FROM public.mission_templates mt
        LEFT JOIN public.mission_completions mc
            ON mc.mission_id = mt.id AND mc.user_id = $2
            AND mc.completion_date = CURRENT_DATE
            AND mc.status NOT IN ('rejected','expired','revoked')
        WHERE mt.community_id = $1 AND mt.status = 'active'
        ORDER BY mt.sort_order ASC, mt.is_featured DESC, mt.xp_reward DESC
    """, community_id, user_id)
    return [dict(r) for r in rows]


@router.post("/submit", response_model=MissionSubmitResponse, status_code=202)
async def submit_mission(
    body:             MissionSubmitRequest,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user),
    db = Depends(get_db),
):
    if not await db.fetchval(
        "SELECT 1 FROM public.community_members WHERE community_id=$1 AND user_id=$2",
        body.community_id, user_id):
        raise HTTPException(403, "Join this community first")

    mission = await db.fetchrow("""
        SELECT id, type, is_daily, max_completions_per_user, available_from, available_until
        FROM public.mission_templates
        WHERE id=$1 AND community_id=$2 AND status='active'
    """, body.mission_id, body.community_id)
    if not mission:
        raise HTTPException(404, "Mission not found or not active")

    if mission["is_daily"]:
        if await db.fetchval("""
            SELECT 1 FROM public.mission_completions
            WHERE mission_id=$1 AND user_id=$2 AND completion_date=$3
            AND status NOT IN ('rejected','expired','revoked')
        """, body.mission_id, user_id, date.today()):
            raise HTTPException(409, "Already completed today")

    completion_id = await db.fetchval("""
        INSERT INTO public.mission_completions
            (mission_id, user_id, community_id, proof_data, completion_date)
        VALUES ($1,$2,$3,$4,$5) RETURNING id
    """, body.mission_id, user_id, body.community_id, body.proof_data, date.today())

    background_tasks.add_task(_verify_in_background, completion_id)

    return MissionSubmitResponse(
        completion_id=completion_id, status=CompletionStatus.pending,
        xp_awarded=0, message="Submission received. Verification in progress.")


async def _verify_in_background(completion_id: UUID):
    from app.database import get_db_pool
    from app.services.verification import verify_completion
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        result = await verify_completion(completion_id, conn)
        logger.info("Verification result", completion_id=str(completion_id), **result)


@router.get("/completions/{community_id}")
async def get_my_completions(
    community_id: UUID,
    limit:  int = Query(20, ge=1, le=100),
    offset: int = Query(0,  ge=0),
    user_id: UUID = Depends(get_current_user),
    db = Depends(get_db),
):
    rows = await db.fetch("""
        SELECT mc.id, mc.status, mc.xp_awarded, mc.completion_date,
               mc.verified_at, mc.rejection_reason, mt.title, mt.type, mt.icon
        FROM public.mission_completions mc
        JOIN public.mission_templates mt ON mc.mission_id = mt.id
        WHERE mc.user_id=$1 AND mc.community_id=$2
        ORDER BY mc.created_at DESC LIMIT $3 OFFSET $4
    """, user_id, community_id, limit, offset)
    return [dict(r) for r in rows]
