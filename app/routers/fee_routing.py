from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request
from uuid import UUID

from app.database import get_db, get_db_pool
from app.middleware.auth import get_current_user
from app.schemas import FeeRoutingSetupResponse, MessageResponse
import structlog

router = APIRouter(prefix="/fee-routing", tags=["fee-routing"])
logger = structlog.get_logger()


@router.get("/{community_id}/setup", response_model=FeeRoutingSetupResponse)
async def get_fee_routing_setup(
    community_id: UUID,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
):
    is_creator = await db.fetchval("""
        SELECT 1 FROM public.communities c
        JOIN public.creators cr ON c.creator_id=cr.id
        JOIN public.users u ON cr.user_id=u.id
        WHERE c.id=$1 AND u.id=$2
    """, community_id, user_id)
    if not is_creator:
        raise HTTPException(403, "Only the creator can access fee routing setup")

    config = await db.fetchrow(
        "SELECT * FROM public.fee_routing_configs WHERE community_id=$1", community_id)

    escrow_wallet = config["community_escrow_wallet"] if config else "UTOPIA_ESCROW_PLACEHOLDER"
    agent_wallet  = config["agent_wallet"] if config else "UTOPIA_AGENT_PLACEHOLDER"

    return FeeRoutingSetupResponse(
        community_id=community_id,
        escrow_wallet=escrow_wallet,
        agent_wallet=agent_wallet,
        suggested_splits={"community_prize_pool":"50%","ai_agent_wallet":"20%","creator_wallet":"30%"},
        instructions=(
            f"1. Vai alla pagina del tuo token su pump.fun o bags.fm\n"
            f"2. Apri Creator Fee settings\n"
            f"3. Aggiungi questi wallet come destinatari:\n"
            f"   • Community Prize Pool (50%): {escrow_wallet}\n"
            f"   • AI Agent (20%): {agent_wallet}\n"
            f"   • Tuo wallet (30%): [il tuo wallet]\n"
            f"4. Conferma — attenzione: su pump.fun si può fare una sola volta\n"
            f"5. Torna qui e clicca 'Confirm Setup'"
        ),
    )


@router.post("/{community_id}/confirm", response_model=MessageResponse)
async def confirm_fee_routing(
    community_id: UUID,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
):
    is_creator = await db.fetchval("""
        SELECT 1 FROM public.communities c
        JOIN public.creators cr ON c.creator_id=cr.id
        JOIN public.users u ON cr.user_id=u.id
        WHERE c.id=$1 AND u.id=$2
    """, community_id, user_id)
    if not is_creator:
        raise HTTPException(403, "Only the creator can confirm fee routing")

    await db.execute("""
        UPDATE public.fee_routing_configs
        SET is_configured=TRUE, configured_at=NOW()
        WHERE community_id=$1
    """, community_id)
    await db.execute(
        "UPDATE public.communities SET fee_routing_enabled=TRUE WHERE id=$1", community_id)
    return MessageResponse(message="Fee routing confirmed. Prize pool accumulating from trades.")


@router.post("/webhook")
async def fee_routing_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")

    tx_signature     = data.get("tx_signature")
    community_id_str = data.get("community_id")
    amounts          = data.get("amounts", {})

    if not tx_signature or not community_id_str:
        raise HTTPException(400, "Missing tx_signature or community_id")

    try:
        community_id = UUID(community_id_str)
    except ValueError:
        raise HTTPException(400, "Invalid community_id")

    pool = await get_db_pool()
    async with pool.acquire() as db:
        if await db.fetchval(
            "SELECT 1 FROM public.fee_routing_events WHERE tx_signature=$1", tx_signature):
            return {"status": "already_processed"}

        config = await db.fetchrow(
            "SELECT id FROM public.fee_routing_configs WHERE community_id=$1", community_id)
        if not config:
            return {"status": "no_config"}

        total     = amounts.get("total",     0)
        community = amounts.get("community", 0)
        agent     = amounts.get("agent",     0)
        creator   = amounts.get("creator",   0)
        burn      = amounts.get("burn",      int(agent * 0.5))

        await db.execute("""
            INSERT INTO public.fee_routing_events
                (config_id, community_id, tx_signature, block_time,
                 total_amount, community_amount, agent_amount, creator_amount, burn_amount)
            VALUES ($1,$2,$3,NOW(),$4,$5,$6,$7,$8)
        """, config["id"], community_id, tx_signature, total, community, agent, creator, burn)

        if community > 0:
            await db.execute("""
                UPDATE public.competitions
                SET fee_accumulated_sol=fee_accumulated_sol+$2, fee_last_updated_at=NOW()
                WHERE community_id=$1 AND status='active'
            """, community_id, community)

        await db.execute("""
            UPDATE public.fee_routing_configs
            SET last_fee_received_at=NOW() WHERE community_id=$1
        """, community_id)

        if agent > 0:
            await db.execute("""
                UPDATE agent.wallet_state
                SET balance_lamports=balance_lamports+$1,
                    total_received=total_received+$1,
                    last_synced_at=NOW()
                WHERE network='mainnet-beta'
            """, agent - burn)

        await db.execute("""
            UPDATE public.fee_routing_events
            SET processed=TRUE, processed_at=NOW()
            WHERE tx_signature=$1
        """, tx_signature)

        logger.info("Fee event processed", tx=tx_signature[:12]+"...",
                    community_amount=community, agent_amount=agent)

    return {"status": "ok"}
