from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator
from psycopg.errors import UniqueViolation

from auth import Actor, current_actor, issue_token
from database import transaction
from models import InputModel, Name

router = APIRouter(prefix="/users", tags=["Usuarios"])


class UserCreate(InputModel):
    name: Name
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def password_bytes(cls, value):
        if len(value.encode()) > 72:
            raise ValueError("La contraseña no puede superar 72 bytes UTF-8")
        return value


class UserLogin(InputModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=72)


def public_user(user):
    return {key: user[key] for key in ("id", "name", "email", "active")}


def require_active_user(conn, user_id):
    user = conn.execute("SELECT * FROM users WHERE id=%s FOR SHARE", (user_id,)).fetchone()
    if not user:
        raise HTTPException(404, "Usuario no encontrado")
    if not user["active"]:
        raise HTTPException(403, "Usuario dado de baja")
    return user


@router.post("/", status_code=201)
def create_user(user: UserCreate):
    hashed = bcrypt.hashpw(user.password.encode(), bcrypt.gensalt()).decode()
    try:
        with transaction() as conn:
            result = conn.execute(
                "INSERT INTO users(name,email,password_hash) VALUES (%s,%s,%s) RETURNING *",
                (user.name, user.email.lower(), hashed),
            ).fetchone()
    except UniqueViolation:
        raise HTTPException(409, "El email ya está registrado") from None
    return public_user(result)


@router.post("/login")
def login(data: UserLogin):
    error = None
    with transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE email=%s FOR UPDATE", (data.email.lower(),)).fetchone()
        now = datetime.now(timezone.utc)
        if not user:
            error = HTTPException(401, "Usuario o contraseña incorrectos")
        elif not user["active"]:
            error = HTTPException(403, "Usuario dado de baja")
        elif user["locked_until"] and user["locked_until"] > now:
            error = HTTPException(401, "Cuenta temporalmente bloqueada")
        elif len(data.password.encode()) > 72 or not bcrypt.checkpw(data.password.encode(), user["password_hash"].encode()):
            attempts = (0 if user["locked_until"] else user["failed_attempts"]) + 1
            locked = now + timedelta(minutes=15) if attempts >= 5 else None
            conn.execute("UPDATE users SET failed_attempts=%s, locked_until=%s WHERE id=%s", (attempts, locked, user["id"]))
            error = HTTPException(401, "Usuario o contraseña incorrectos")
        else:
            conn.execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=%s", (user["id"],))
    # Los intentos fallidos deben persistir incluso cuando respondemos 401.
    if error:
        raise error
    return {"access_token": issue_token(user["id"]), "token_type": "bearer", "expires_in": 3600, "user": public_user(user)}


@router.delete("/{user_id}")
def delete_user(user_id: int, actor: Actor = Depends(current_actor)):
    if not actor.admin and actor.user_id != user_id:
        raise HTTPException(403, "No autorizado")
    with transaction() as conn:
        result = conn.execute("UPDATE users SET active=FALSE WHERE id=%s RETURNING id", (user_id,)).fetchone()
        if not result:
            raise HTTPException(404, "Usuario no encontrado")
    return {"message": "Usuario dado de baja"}
