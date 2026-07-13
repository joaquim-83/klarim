"""Autenticação de contas de usuário (KL-51 f3) — separada do operador/admin.

O admin é um operador único (ADMIN_USER/ADMIN_PASSWORD, token Bearer de 24h). Aqui
são as contas dos **donos de site**: senha com bcrypt, JWT de 30 dias no **cookie**
HttpOnly. Os dois tipos de token são assinados com o mesmo `JWT_SECRET`, então cada
um carrega um claim `typ` (`"admin"` | `"user"`) e cada camada só aceita o seu — sem
isso, um cookie de usuário passaria no middleware admin (mesma assinatura).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import HTTPException, Request

USER_COOKIE = "klarim_session"
USER_JWT_TTL = 30 * 24 * 3600  # 30 dias
_JWT_ALGO = "HS256"


# --- senha (bcrypt) --------------------------------------------------------- #
def hash_password(password: str) -> str:
    # bcrypt trunca em 72 bytes; truncamos explicitamente para evitar surpresa.
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], (hashed or "").encode("utf-8"))
    except Exception:  # noqa: BLE001 - hash malformado nunca deve levantar
        return False


# --- JWT de usuário (cookie, 30 dias) --------------------------------------- #
def _secret() -> str:
    return os.environ.get("JWT_SECRET", "")


def create_user_token(user: dict) -> str:
    import jwt

    now = datetime.now(timezone.utc)
    payload = {
        "typ": "user",
        "user_id": int(user["id"]),
        "email": user["email"],
        "plan": user.get("plan", "free"),
        "iat": now,
        "exp": now + timedelta(seconds=USER_JWT_TTL),
    }
    return jwt.encode(payload, _secret(), algorithm=_JWT_ALGO)


def verify_user_token(token: str) -> dict:
    """Decodifica/valida o JWT de usuário. Levanta se inválido/expirado ou se o
    `typ` não é `user` (ex.: alguém tentando usar um token de admin aqui)."""
    import jwt

    payload = jwt.decode(token, _secret(), algorithms=[_JWT_ALGO])
    if payload.get("typ") != "user" or not payload.get("user_id"):
        raise ValueError("token não é de usuário")
    return payload


def token_from_request(request: Request) -> str:
    """Token do usuário: `Authorization: Bearer` (fetch SSR do Astro) ou o cookie
    `klarim_session` (navegador)."""
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return request.cookies.get(USER_COOKIE, "") or ""


async def require_user(request: Request) -> dict:
    """Dependency FastAPI: exige um usuário autenticado. Retorna o dict do user
    (sem hash). 401 se ausente/inválido/expirado ou conta inativa."""
    token = token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Não autenticado.")
    try:
        payload = verify_user_token(token)
    except Exception:  # noqa: BLE001 - qualquer falha de token → 401
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada.")
    from discovery.store import get_target_store

    user = await get_target_store().get_user_by_id(int(payload["user_id"]))
    if not user or not user.get("is_active", True):
        raise HTTPException(status_code=401, detail="Conta não encontrada.")
    return user


async def optional_user(request: Request) -> Optional[dict]:
    """Como `require_user`, mas devolve ``None`` em vez de 401 (para o Header/CTA)."""
    try:
        return await require_user(request)
    except HTTPException:
        return None
