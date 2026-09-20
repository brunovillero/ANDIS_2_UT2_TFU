from uuid import UUID
from fastapi import APIRouter, Depends
from pydantic import Field

from auth import Actor, require_admin
from cards import get_card_record, require_active_card, debit, record_movement
from rides import get_ride_record, reserve_place
from database import transaction
from idempotency import execute_once
from models import InputModel

router = APIRouter(prefix="/access", tags=["Acceso"])


class AccessRequest(InputModel):
    card_id: int = Field(gt=0)
    ride_id: int = Field(gt=0)
    operation_id: UUID


@router.post("/validate")
def validate_access(data: AccessRequest, actor: Actor = Depends(require_admin)):
    with transaction() as conn:
        def action():
            # Mismo orden de locks en todos los accesos: tarjeta, usuario, atracción.
            card = get_card_record(conn, data.card_id, lock=True)
            require_active_card(conn, card)
            ride = get_ride_record(conn, data.ride_id, lock=True)
            # Si el débito falla, la reserva previa se revierte: una única transacción.
            occupancy = reserve_place(conn, ride)
            balance = debit(conn, card, ride["price"])
            record_movement(conn, card["id"], "ride_access", ride["price"], ride["id"])
            return {
                "message": "Acceso permitido", "card_id": card["id"], "ride_id": ride["id"],
                "charged": ride["price"], "remaining_balance": balance,
                "current_occupancy": occupancy,
            }
        result = execute_once(conn, actor, data.operation_id, "access", {"card_id": data.card_id, "ride_id": data.ride_id}, action)
    return result
