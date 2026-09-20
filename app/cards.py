from decimal import Decimal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from auth import Actor, current_actor, require_admin, require_owner
from database import transaction
from idempotency import execute_once
from models import InputModel, Money
from users import require_active_user

router = APIRouter(prefix="/cards", tags=["Tarjetas"])


class CardCreate(InputModel):
    user_id: int = Field(gt=0)


class CardRecharge(InputModel):
    amount: Money
    operation_id: UUID


# Interfaz interna ITarjeta: todas estas operaciones usan la unidad de trabajo del llamador.
def get_card_record(conn, card_id, lock=False):
    sql = "SELECT * FROM cards WHERE id=%s" + (" FOR UPDATE" if lock else "")
    card = conn.execute(sql, (card_id,)).fetchone()
    if not card:
        raise HTTPException(404, "Tarjeta no encontrada")
    return card


def require_active_card(conn, card):
    if not card["active"]:
        raise HTTPException(403, "Tarjeta dada de baja")
    require_active_user(conn, card["user_id"])


def debit(conn, card, amount):
    if card["balance"] < amount:
        raise HTTPException(403, "Saldo insuficiente")
    return conn.execute("UPDATE cards SET balance=balance-%s WHERE id=%s RETURNING balance", (amount, card["id"])).fetchone()["balance"]


def record_movement(conn, card_id, kind, amount, ride_id=None):
    conn.execute("INSERT INTO movements(card_id,type,amount,ride_id) VALUES (%s,%s,%s,%s)", (card_id, kind, amount, ride_id))


@router.post("/", status_code=201)
def create_card(data: CardCreate, actor: Actor = Depends(current_actor)):
    require_owner(actor, data.user_id)
    with transaction() as conn:
        require_active_user(conn, data.user_id)
        result = conn.execute("INSERT INTO cards(user_id) VALUES (%s) RETURNING *", (data.user_id,)).fetchone()
    return result


@router.get("/{card_id}")
def get_card(card_id: int, actor: Actor = Depends(current_actor)):
    with transaction() as conn:
        result = get_card_record(conn, card_id)
        require_owner(actor, result["user_id"])
    return result


@router.delete("/{card_id}")
def delete_card(card_id: int, actor: Actor = Depends(current_actor)):
    with transaction() as conn:
        card = get_card_record(conn, card_id, lock=True)
        require_owner(actor, card["user_id"])
        conn.execute("UPDATE cards SET active=FALSE WHERE id=%s", (card_id,))
    return {"message": "Tarjeta dada de baja"}


@router.patch("/{card_id}/recharge")
def recharge_card(card_id: int, data: CardRecharge, actor: Actor = Depends(require_admin)):
    with transaction() as conn:
        def action():
            card = get_card_record(conn, card_id, lock=True)
            require_active_card(conn, card)
            if card["balance"] + data.amount > Decimal("9999999999.99"):
                raise HTTPException(422, "Saldo máximo excedido")
            result = conn.execute("UPDATE cards SET balance=balance+%s WHERE id=%s RETURNING *", (data.amount, card_id)).fetchone()
            record_movement(conn, card_id, "recharge", data.amount)
            return result
        result = execute_once(conn, actor, data.operation_id, "recharge", {"card_id": card_id, "amount": format(data.amount, '.2f')}, action)
    return result


@router.get("/{card_id}/history")
def get_card_history(card_id: int, actor: Actor = Depends(current_actor)):
    with transaction() as conn:
        card = get_card_record(conn, card_id)
        require_owner(actor, card["user_id"])
        rows = conn.execute("SELECT * FROM movements WHERE card_id=%s ORDER BY id", (card_id,)).fetchall()
    return {"card_id": card_id, "history": rows}
