"""Простая Bearer-авторизация для write-эндпоинтов."""
import logging
import os

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger("auth")

API_TOKEN = os.getenv("API_TOKEN", "")

security = HTTPBearer(auto_error=False)


def require_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> None:
    """
    Проверяет заголовок Authorization: Bearer <token>.

    Если API_TOKEN не задан в .env — работает в dev-режиме (без проверки).
    """
    if not API_TOKEN:
        return

    if credentials is None or credentials.credentials != API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )