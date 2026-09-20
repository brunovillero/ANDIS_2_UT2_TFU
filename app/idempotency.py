"""El resultado y los efectos se confirman en la misma transacción PostgreSQL."""
import hashlib
import json
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb


def execute_once(conn, actor, operation_id, kind, payload, action):
    fingerprint = json.dumps([kind, jsonable_encoder(payload)], sort_keys=True)
    # Bloqueo entre réplicas, liberado automáticamente en commit/rollback.
    digest = hashlib.sha256(f"{actor.key}:{operation_id}".encode()).digest()
    lock_id = int.from_bytes(digest[:8], "big", signed=True)
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_id,))
    previous = conn.execute(
        "SELECT fingerprint, response FROM operations WHERE actor=%s AND operation_id=%s",
        (actor.key, operation_id),
    ).fetchone()
    if previous:
        if previous["fingerprint"] != fingerprint:
            raise HTTPException(409, "operation_id ya fue utilizado con otros datos")
        return previous["response"]
    response = jsonable_encoder(action())
    conn.execute(
        "INSERT INTO operations(actor, operation_id, fingerprint, response) VALUES (%s,%s,%s,%s)",
        (actor.key, operation_id, fingerprint, Jsonb(response)),
    )
    return response
