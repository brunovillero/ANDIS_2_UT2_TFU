"""Credencial administrativa de demo y tokens de usuario firmados, sin sesión local."""
import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from database import transaction

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "TFU-UT2-SECRET-KEY")
TOKEN_SECRET = os.environ.get("TOKEN_SECRET", "solo-demo-cambiar-antes-de-publicar")
security = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Actor:
    user_id: int | None = None
    admin: bool = False

    @property
    def key(self):
        return "admin" if self.admin else f"user:{self.user_id}"


def issue_token(user_id):
    payload = f"{user_id}:{int(time.time()) + 3600}"
    signature = hmac.new(TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()


def current_actor(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
    if credentials is None:
        raise HTTPException(401, "Se requiere autenticación", headers={"WWW-Authenticate": "Bearer"})
    token = credentials.credentials
    if hmac.compare_digest(token.encode(), ADMIN_TOKEN.encode()):
        return Actor(admin=True)
    try:
        user_id, expires, signature = base64.urlsafe_b64decode(token).decode().split(":")
        payload = f"{user_id}:{expires}"
        expected = hmac.new(TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature.encode(), expected.encode()) or int(expires) <= time.time():
            raise ValueError()
        actor = Actor(user_id=int(user_id))
    except (ValueError, UnicodeError):
        raise HTTPException(401, "Token inválido o vencido") from None
    with transaction() as conn:
        user = conn.execute("SELECT active FROM users WHERE id = %s", (actor.user_id,)).fetchone()
    if not user or not user["active"]:
        raise HTTPException(401, "Usuario inactivo o inexistente")
    return actor


def require_admin(actor: Actor = Depends(current_actor)):
    if not actor.admin:
        raise HTTPException(403, "Se requiere una credencial de operador/administrador")
    return actor


def require_owner(actor, user_id):
    if not actor.admin and actor.user_id != user_id:
        raise HTTPException(403, "La tarjeta no pertenece al usuario")
