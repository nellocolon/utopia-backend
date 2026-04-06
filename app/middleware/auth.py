from __future__ import annotations
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from datetime import datetime, timedelta
from uuid import UUID
from app.config import get_settings

settings = get_settings()
security = HTTPBearer()

ALGORITHM     = "HS256"
ACCESS_EXPIRE = timedelta(days=30)


def create_access_token(user_id: UUID, x_handle: str | None = None) -> str:
    payload = {
        "sub": str(user_id),
        "x_handle": x_handle,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + ACCESS_EXPIRE,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> UUID:
    payload = decode_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing subject")
    return UUID(user_id)


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
) -> UUID | None:
    if not credentials:
        return None
    try:
        return await get_current_user(credentials)
    except HTTPException:
        return None
