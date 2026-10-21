from datetime import datetime, timedelta, timezone

import jwt

from ..config import settings

TOKEN_TYPE = "access"


def create_access_token(subject: str, token_version: int = 0, expires_minutes: int | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    payload = {"sub": subject, "type": TOKEN_TYPE, "exp": expire, "tv": token_version}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> dict:
    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    if payload.get("type") != TOKEN_TYPE:
        raise jwt.InvalidTokenError("Unexpected token type")
    return payload
