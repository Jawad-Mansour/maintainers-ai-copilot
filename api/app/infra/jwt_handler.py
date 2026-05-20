"""JWT creation and verification.

Signing key is fetched from Vault at startup — never from env or config files.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt

from app.exceptions import AuthenticationError

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours


def create_access_token(user_id: str, role: str, signing_key: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "role": role, "exp": expire}
    return jwt.encode(payload, signing_key, algorithm=ALGORITHM)


def decode_access_token(token: str, signing_key: str) -> dict[str, object]:
    try:
        payload: dict[str, object] = jwt.decode(token, signing_key, algorithms=[ALGORITHM])
        return payload
    except JWTError as exc:
        raise AuthenticationError("Invalid or expired token") from exc
