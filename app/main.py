from contextlib import asynccontextmanager
import logging
import socket

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import Field
import psycopg

import access
import cards
import rides
import users
from auth import Actor, current_actor
from database import initialize, transaction
from models import InputModel, Name


@asynccontextmanager
async def lifespan(app):
    initialize()
    yield


app = FastAPI(title="Acceso Rodó — TFU 3", version="3.0.0", lifespan=lifespan)
for router in (users.router, cards.router, rides.router, access.router):
    app.include_router(router)


@app.middleware("http")
async def identify_replica(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Replica-ID"] = socket.gethostname()
    return response


@app.exception_handler(psycopg.OperationalError)
async def database_unavailable(request, exc):
    logging.getLogger(__name__).warning("Operación de base no disponible: %s", type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Base de datos no disponible; reintentar con el mismo operation_id"})


@app.get("/health")
def health():
    with transaction() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok", "processed_by": socket.gethostname()}


class PayloadModel(InputModel):
    item_id: int = Field(gt=0)
    name: Name


@app.post("/process", tags=["Compatibilidad TFU 2"])
def process_data(data: PayloadModel, actor: Actor = Depends(current_actor)):
    return {"status": "success", "processed_by": socket.gethostname(), "received": data.model_dump()}
