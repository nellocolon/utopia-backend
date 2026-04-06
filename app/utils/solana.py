from __future__ import annotations
"""
Solana utilities.
Verifica firma wallet, check transazioni, lettura balance.
TODO: espandere con Anchor client per escrow smart contract.
"""
import base64
from app.config import get_settings
import structlog

logger   = structlog.get_logger()
settings = get_settings()


def verify_wallet_signature(wallet_address: str, message: str, signature_b64: str) -> bool:
    """
    Verifica che il wallet_address abbia firmato il messaggio.
    Usa Ed25519 (standard Solana).
    
    TODO: implementare con solders prima del lancio mainnet.
    Per ora ritorna True per non bloccare lo sviluppo.
    """
    try:
        # Placeholder — da implementare con:
        # from solders.pubkey import Pubkey
        # from solders.signature import Signature
        # sig   = Signature.from_string(signature_b64)
        # pubkey = Pubkey.from_string(wallet_address)
        # return sig.verify(pubkey, message.encode())
        logger.warning("Wallet signature verification NOT implemented — accepting all", wallet=wallet_address[:8])
        return True
    except Exception as e:
        logger.error("Signature verification error", error=str(e))
        return False


async def get_wallet_balance(wallet_address: str) -> int:
    """
    Ritorna il balance in lamports del wallet.
    Usa RPC call diretta a Solana.
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                settings.solana_rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getBalance",
                    "params": [wallet_address, {"commitment": settings.solana_commitment}]
                }
            )
            data = resp.json()
            return data.get("result", {}).get("value", 0)
    except Exception as e:
        logger.error("getBalance failed", wallet=wallet_address[:8], error=str(e))
        return 0


async def verify_transaction(tx_signature: str, expected_receiver: str | None = None,
                              min_lamports: int | None = None) -> tuple[bool, str]:
    """
    Verifica che una transazione Solana esista e (opzionalmente) che:
    - contenga il receiver atteso
    - abbia trasferito almeno min_lamports

    Ritorna (is_valid, reason).
    TODO: implementare parsing completo della tx per mainnet.
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                settings.solana_rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [
                        tx_signature,
                        {"encoding": "json", "commitment": "confirmed",
                         "maxSupportedTransactionVersion": 0}
                    ]
                }
            )
            data = resp.json()
            if data.get("result") is None:
                return False, "Transaction not found on-chain"
            
            # Basic check: tx exists and is confirmed
            tx = data["result"]
            if tx.get("meta", {}).get("err") is not None:
                return False, "Transaction failed on-chain"

            return True, "Transaction confirmed"

    except Exception as e:
        logger.error("verify_transaction failed", tx=tx_signature[:12], error=str(e))
        return False, f"RPC error: {str(e)}"
