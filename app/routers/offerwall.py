from __future__ import annotations
import hmac
import hashlib
from fastapi import APIRouter, Request, HTTPException
from app.database import get_db_pool
from app.config import get_settings
from uuid import UUID
import structlog

router   = APIRouter(prefix="/offerwall", tags=["offerwall"])
logger   = structlog.get_logger()
settings = get_settings()


async def _process_postback(provider_name, transaction_id, user_id_str,
                             offer_id, offer_name, payout_usd, raw_data):
    try:
        user_id = UUID(user_id_str)
    except ValueError:
        logger.warning("Invalid user_id in postback", provider=provider_name)
        return

    pool = await get_db_pool()
    async with pool.acquire() as db:
        provider = await db.fetchrow(
            "SELECT id FROM public.offerwall_providers WHERE name=$1 AND is_active=TRUE",
            provider_name)
        if not provider:
            return

        if await db.fetchval("""
            SELECT 1 FROM public.offerwall_completions
            WHERE provider_id=$1 AND provider_transaction_id=$2
        """, provider["id"], transaction_id):
            logger.info("Duplicate postback ignored", transaction_id=transaction_id)
            return

        community_row = await db.fetchrow("""
            SELECT community_id FROM public.community_members
            WHERE user_id=$1 ORDER BY joined_at DESC LIMIT 1
        """, user_id)
        if not community_row:
            return

        community_id  = community_row["community_id"]
        utopia_share  = round((payout_usd or 0) * 0.70, 4)
        creator_share = round((payout_usd or 0) * 0.30, 4)

        mission = await db.fetchrow("""
            SELECT id, xp_reward FROM public.mission_templates
            WHERE community_id=$1 AND offerwall_provider=$2
              AND offerwall_offer_id=$3 AND status='active' LIMIT 1
        """, community_id, provider_name, offer_id)

        xp_reward = mission["xp_reward"] if mission else 200

        completion_id = await db.fetchval("""
            INSERT INTO public.mission_completions
                (mission_id, user_id, community_id, status, verification_method,
                 verified_at, verified_by, proof_data, completion_date)
            VALUES ($1,$2,$3,'verified','offerwall_callback',NOW(),$4,$5,CURRENT_DATE)
            RETURNING id
        """, mission["id"] if mission else None, user_id, community_id,
            f"offerwall:{provider_name}",
            {"transaction_id": transaction_id, "provider": provider_name})

        await db.fetchval("""
            INSERT INTO public.offerwall_completions
                (provider_id, user_id, community_id, mission_completion_id,
                 provider_transaction_id, offer_id, offer_name,
                 payout_usd, utopia_share_usd, creator_share_usd, raw_postback)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        """, provider["id"], user_id, community_id, completion_id,
            transaction_id, offer_id, offer_name,
            payout_usd, utopia_share, creator_share, raw_data)

        if mission:
            await db.fetchval(
                "SELECT public.award_xp($1,$2,$3,$4,$5,1.00,'mission')",
                user_id, community_id, mission["id"], completion_id, xp_reward)
        else:
            await db.execute("""
                UPDATE public.community_members
                SET xp_total=xp_total+$3, xp_this_week=xp_this_week+$3
                WHERE user_id=$1 AND community_id=$2
            """, user_id, community_id, xp_reward)
            await db.execute("""
                INSERT INTO analytics.xp_events (user_id,community_id,xp_amount,source)
                VALUES ($1,$2,$3,'offerwall')
            """, user_id, community_id, xp_reward)

        await db.execute("""
            UPDATE public.creators cr
            SET pending_payout_usd=pending_payout_usd+$2,
                total_revenue_usd=total_revenue_usd+$3
            FROM public.communities c
            WHERE c.creator_id=cr.id AND c.id=$1
        """, community_id, creator_share, payout_usd or 0)

        logger.info("Offerwall processed", provider=provider_name,
                    transaction_id=transaction_id, xp=xp_reward)


@router.post("/postback/offertoro")
async def offertoro_postback(request: Request):
    params = dict(request.query_params)
    sig = params.pop("sig", "")
    if settings.offertoro_postback_secret:
        payload  = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        expected = hmac.new(settings.offertoro_postback_secret.encode(),
                            payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise HTTPException(403, "Invalid signature")
    await _process_postback("offertoro", params.get("transaction_id",""),
                             params.get("user_id",""), params.get("offer_id"),
                             params.get("offer_name"), float(params.get("amount",0) or 0), params)
    return "1"


@router.post("/postback/adgate")
async def adgate_postback(request: Request):
    params = dict(request.query_params)
    sig = params.pop("signature", "")
    if settings.adgate_postback_secret:
        expected = hashlib.md5(
            (params.get("tid","") + settings.adgate_postback_secret).encode()
        ).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise HTTPException(403, "Invalid signature")
    await _process_postback("adgate", params.get("tid",""), params.get("user_id",""),
                             params.get("oid"), params.get("offer_name"),
                             float(params.get("payout",0) or 0), params)
    return "OK"


@router.post("/postback/freecash")
async def freecash_postback(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = dict(request.query_params)
    await _process_postback("freecash", str(body.get("ref_id","")),
                             str(body.get("user_id","")), str(body.get("offer_id","")),
                             body.get("offer_name"), float(body.get("amount_usd",0) or 0), body)
    return {"status": "ok"}
