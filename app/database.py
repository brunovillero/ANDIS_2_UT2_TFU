"""Unidad de trabajo: una conexión/transacción por operación, compartida entre módulos."""
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://accesorodo:demo@db:5432/accesorodo")


@contextmanager
def transaction():
    # El context manager confirma al terminar y revierte ante cualquier excepción.
    with psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=5) as conn:
        conn.execute("SET LOCAL lock_timeout = '5s'")
        conn.execute("SET LOCAL statement_timeout = '10s'")
        yield conn


def initialize():
    with transaction() as conn:
        # Las tres réplicas pueden arrancar juntas: serializamos la inicialización.
        conn.execute("SELECT pg_advisory_xact_lock(73003)")
        conn.execute(Path(__file__).with_name("schema.sql").read_text())
