from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from auth import Actor, current_actor, require_admin
from database import transaction
from idempotency import execute_once
from models import InputModel, Money, Name

router = APIRouter(prefix="/rides", tags=["Atracciones"])


class RideCreate(InputModel):
    name: Name
    price: Money
    capacity: int = Field(gt=0, le=10000)


class RideUpdate(InputModel):
    name: Name | None = None
    price: Money | None = None
    capacity: int | None = Field(default=None, gt=0, le=10000)


class RideStatusUpdate(InputModel):
    status: Literal["active", "out_of_service"]


class RoundFinish(InputModel):
    operation_id: UUID


# Interfaz interna IAtraccion.
def get_ride_record(conn, ride_id, lock=False):
    sql = "SELECT * FROM rides WHERE id=%s AND status <> 'deleted'" + (" FOR UPDATE" if lock else "")
    ride = conn.execute(sql, (ride_id,)).fetchone()
    if not ride:
        raise HTTPException(404, "Atracción no encontrada")
    return ride


def reserve_place(conn, ride):
    if ride["status"] != "active":
        raise HTTPException(403, "Atracción fuera de servicio")
    if ride["current_occupancy"] >= ride["capacity"]:
        raise HTTPException(403, "Capacidad máxima alcanzada")
    return conn.execute("UPDATE rides SET current_occupancy=current_occupancy+1 WHERE id=%s RETURNING current_occupancy", (ride["id"],)).fetchone()["current_occupancy"]


@router.post("/", status_code=201)
def create_ride(data: RideCreate, actor: Actor = Depends(require_admin)):
    with transaction() as conn:
        result = conn.execute("INSERT INTO rides(name,price,capacity) VALUES (%s,%s,%s) RETURNING *", (data.name, data.price, data.capacity)).fetchone()
    return result


@router.get("/{ride_id}")
def get_ride(ride_id: int, actor: Actor = Depends(current_actor)):
    with transaction() as conn:
        result = get_ride_record(conn, ride_id)
    return result


@router.patch("/{ride_id}")
def update_ride(ride_id: int, data: RideUpdate, actor: Actor = Depends(require_admin)):
    with transaction() as conn:
        ride = get_ride_record(conn, ride_id, lock=True)
        changes = data.model_dump(exclude_unset=True)
        if not changes or any(value is None for value in changes.values()):
            raise HTTPException(422, "Enviar al menos un campo no nulo")
        if changes.get("capacity", ride["capacity"]) < ride["current_occupancy"]:
            raise HTTPException(409, "Capacidad menor a la ocupación actual")
        ride.update(changes)
        result = conn.execute("UPDATE rides SET name=%s, price=%s, capacity=%s WHERE id=%s RETURNING *", (ride["name"], ride["price"], ride["capacity"], ride_id)).fetchone()
    return result


@router.patch("/{ride_id}/status")
def change_ride_status(ride_id: int, data: RideStatusUpdate, actor: Actor = Depends(require_admin)):
    with transaction() as conn:
        get_ride_record(conn, ride_id, lock=True)
        result = conn.execute("UPDATE rides SET status=%s WHERE id=%s RETURNING *", (data.status, ride_id)).fetchone()
    return result


@router.delete("/{ride_id}")
def delete_ride(ride_id: int, actor: Actor = Depends(require_admin)):
    with transaction() as conn:
        ride = get_ride_record(conn, ride_id, lock=True)
        if ride["current_occupancy"]:
            raise HTTPException(409, "Finalizar la ronda antes de eliminar la atracción")
        conn.execute("UPDATE rides SET status='deleted' WHERE id=%s", (ride_id,))
    return {"message": "Atracción eliminada; historial conservado"}


@router.post("/{ride_id}/finish-round")
def finish_round(ride_id: int, data: RoundFinish, actor: Actor = Depends(require_admin)):
    with transaction() as conn:
        def action():
            get_ride_record(conn, ride_id, lock=True)
            return conn.execute("UPDATE rides SET current_occupancy=0 WHERE id=%s RETURNING *", (ride_id,)).fetchone()
        result = execute_once(conn, actor, data.operation_id, "finish_round", {"ride_id": ride_id}, action)
    return result
