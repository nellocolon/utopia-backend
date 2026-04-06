from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
import httpx
import secrets
import hashlib
import base64
from uuid import UUID

from app.config import get_settings
from app.database import get_supabase_admin, get_db
from app.middleware.auth import create_access_token, get_current_user
from app.schemas import AuthResponse, WalletConnectRequest, UserProfile, MessageResponse
import structlog

router   = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()
logger   = structlog.get_logger()

_pkce_store: dict[str, str] = {}


def _generate_code_verifier() -> str:
    return secrets.token_urlsafe(64)

def _generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@router.get("/x/init")
async def x_oauth_init():
    verifier  = _generate_code_verifier()
    challenge = _generate_code_challenge(verifier)
    state     = secrets.token_urlsafe(16)
    _pkce_store[state] = verifier
    params = {
        "response_type":         "code",
        "client_id":             settings.x_client_id,
        "redirect_uri":          settings.x_callback_url,
        "scope":                 "tweet.read users.read offline.access",
        "state":                 state,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return {"redirect_url": f"https://twitter.com/i/oauth2/authorize?{qs}"}


@router.get("/x/callback")
async def x_oauth_callback(
    code:  str = Query(...),
    state: str = Query(...),
    db = Depends(get_db),
):
    verifier = _pkce_store.pop(state, None)
    if not verifier:
        raise HTTPException(400, "Invalid or expired OAuth state")

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://api.twitter.com/2/oauth2/token",
            data={"code": code, "grant_type": "authorization_code",
                  "redirect_uri": settings.x_callback_url, "code_verifier": verifier},
            auth=(settings.x_client_id, settings.x_client_secret),
        )

    if token_resp.status_code != 200:
        raise HTTPException(400, "X OAuth token exchange failed")

    x_access_token = token_resp.json().get("access_token")

    async with httpx.AsyncClient() as client:
        user_resp = await client.get(
            "https://api.twitter.com/2/users/me",
            params={"user.fields": "id,name,username,profile_image_url,public_metrics,verified"},
            headers={"Authorization": f"Bearer {x_access_token}"},
        )

    if user_resp.status_code != 200:
        raise HTTPException(400, "Failed to fetch X user profile")

    x_user = user_resp.json().get("data", {})
    x_id   = x_user.get("id")
    if not x_id:
        raise HTTPException(400, "X user ID not found")

    admin    = get_supabase_admin()
    existing = admin.table("users").select("id").eq("x_id", x_id).maybe_single().execute()

    if existing.data:
        user_id = UUID(existing.data["id"])
        admin.table("users").update({
            "x_handle":          x_user.get("username"),
            "x_display_name":    x_user.get("name"),
            "x_avatar_url":      x_user.get("profile_image_url"),
            "x_followers_count": x_user.get("public_metrics", {}).get("followers_count", 0),
            "x_oauth_token":     x_access_token,
            "last_active_at":    "now()",
        }).eq("id", str(user_id)).execute()
    else:
        auth_resp = admin.auth.admin.create_user({
            "email": f"{x_id}@x.utopia.placeholder",
            "password": secrets.token_urlsafe(32),
            "email_confirm": True,
        })
        auth_id  = auth_resp.user.id
        new_user = admin.table("users").insert({
            "auth_id":           str(auth_id),
            "x_id":              x_id,
            "x_handle":          x_user.get("username"),
            "x_display_name":    x_user.get("name"),
            "x_avatar_url":      x_user.get("profile_image_url"),
            "x_followers_count": x_user.get("public_metrics", {}).get("followers_count", 0),
            "x_oauth_token":     x_access_token,
        }).execute()
        user_id = UUID(new_user.data[0]["id"])

    token = create_access_token(user_id=user_id, x_handle=x_user.get("username"))
    return AuthResponse(access_token=token, user_id=user_id,
                        x_handle=x_user.get("username"), needs_wallet=existing.data is None)


@router.post("/wallet", response_model=MessageResponse)
async def connect_wallet(
    body: WalletConnectRequest,
    user_id: UUID = Depends(get_current_user),
    db = Depends(get_db),
):
    expected_message = f"UTOPIA_CONNECT:{user_id}"
    if body.message != expected_message:
        raise HTTPException(400, "Invalid message for signature verification")
    admin = get_supabase_admin()
    admin.table("users").update({
        "wallet_address":     body.wallet_address,
        "wallet_verified_at": "now()",
    }).eq("id", str(user_id)).execute()
    return MessageResponse(message="Wallet connected successfully")


@router.get("/me", response_model=UserProfile)
async def get_me(user_id: UUID = Depends(get_current_user), db = Depends(get_db)):
    row = await db.fetchrow(
        "SELECT * FROM public.users WHERE id = $1 AND deleted_at IS NULL", user_id)
    if not row:
        raise HTTPException(404, "User not found")
    return dict(row)
