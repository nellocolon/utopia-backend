from __future__ import annotations
import asyncpg
from uuid import UUID
from datetime import datetime, timezone
from app.services.x_api import (
    verify_post_mission, verify_retweet_mission, verify_clip_mission
)
import structlog

logger = structlog.get_logger()


async def verify_completion(completion_id: UUID, db: asyncpg.Connection) -> dict:
    row = await db.fetchrow("""
        SELECT mc.id, mc.user_id, mc.community_id, mc.mission_id,
               mc.proof_data, mc.status,
               mt.verification_method, mt.verification_data,
               mt.xp_reward, mt.type AS mission_type,
               u.x_handle,
               cm.xp_multiplier, cm.xp_multiplier_expires_at
        FROM public.mission_completions mc
        JOIN public.mission_templates mt ON mc.mission_id = mt.id
        JOIN public.users u ON mc.user_id = u.id
        JOIN public.community_members cm
            ON mc.user_id = cm.user_id AND mc.community_id = cm.community_id
        WHERE mc.id = $1
    """, completion_id)

    if not row:
        return {"success": False, "xp_awarded": 0, "reason": "Completion not found"}
    if row["status"] != "pending":
        return {"success": False, "xp_awarded": 0, "reason": f"Already {row['status']}"}

    method       = row["verification_method"]
    proof        = row["proof_data"] or {}
    vdata        = row["verification_data"] or {}
    x_handle     = row["x_handle"]
    is_valid     = False
    reason       = "Unknown method"
    extra_proof  = {}

    try:
        if method == "api_x":
            mtype  = row["mission_type"]
            action = vdata.get("action", "post")

            if mtype == "daily":
                if action == "retweet":
                    is_valid, reason = await verify_retweet_mission(
                        x_handle, vdata.get("tweet_id", proof.get("retweeted_id", "")))
                else:
                    is_valid, reason = await verify_post_mission(
                        x_handle, proof.get("tweet_id", ""),
                        keyword=vdata.get("keyword"), min_length=vdata.get("min_length", 0))
            elif mtype == "clipping":
                is_valid, reason, extra_proof = await verify_clip_mission(
                    x_handle, proof.get("tweet_id", ""),
                    quality_threshold=vdata.get("quality_threshold", 0.3))
            else:
                is_valid, reason = await verify_post_mission(
                    x_handle, proof.get("tweet_id", ""))

        elif method == "offerwall_callback":
            is_valid = True
            reason   = "Verified via offerwall postback"

        elif method == "onchain":
            is_valid = bool(proof.get("tx_signature"))
            reason   = "On-chain tx found" if is_valid else "No tx signature provided"

        elif method == "self_report":
            is_valid = True
            reason   = "Self-reported — flagged for review"

        elif method == "manual":
            is_valid = False
            reason   = "Manual verification required"

    except Exception as e:
        logger.error("Verification exception", completion_id=str(completion_id), error=str(e))
        await _mark_rejected(db, completion_id, f"Service error: {str(e)}")
        return {"success": False, "xp_awarded": 0, "reason": str(e)}

    if is_valid:
        multiplier = float(row["xp_multiplier"] or 1.0)
        expires    = row["xp_multiplier_expires_at"]
        if expires and expires < datetime.now(timezone.utc):
            multiplier = 1.0

        await db.execute("""
            UPDATE public.mission_completions
            SET status='verified', verified_at=NOW(), verified_by='system',
                proof_data=proof_data || $2::jsonb
            WHERE id=$1
        """, completion_id, extra_proof)

        xp_awarded = await db.fetchval(
            "SELECT public.award_xp($1,$2,$3,$4,$5,$6,'mission')",
            row["user_id"], row["community_id"], row["mission_id"],
            completion_id, row["xp_reward"], multiplier)

        logger.info("Mission verified", completion_id=str(completion_id), xp=xp_awarded)
        return {"success": True, "xp_awarded": xp_awarded, "reason": reason}

    await _mark_rejected(db, completion_id, reason)
    return {"success": False, "xp_awarded": 0, "reason": reason}


async def _mark_rejected(db, completion_id: UUID, reason: str):
    await db.execute("""
        UPDATE public.mission_completions
        SET status='rejected', verified_at=NOW(), verified_by='system', rejection_reason=$2
        WHERE id=$1
    """, completion_id, reason)
