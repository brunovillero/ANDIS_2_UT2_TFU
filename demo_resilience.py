"""Demuestra failover, estado compartido y durabilidad sin borrar datos existentes."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import uuid

sys.path.insert(0, str(Path(__file__).parent / "tests"))
from test_api import api, operation

DOCKER = shutil.which("docker") or str(Path.home() / ".docker/bin/docker")


def compose(*args):
    subprocess.run([DOCKER, "compose", *args], cwd=Path(__file__).parent, check=True)


def request(method, path, body=None, token=None):
    from test_api import ADMIN
    status, result, replica = api(method, path, body, ADMIN if token is None else token)
    if status not in (200, 201):
        raise RuntimeError((status, result))
    return result, replica


def wait_ready():
    for _ in range(60):
        try:
            if api("GET", "/health")[0] == 200:
                return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError("La API no quedó disponible")


def main():
    email = str(uuid.uuid4()) + "@resilience.test"
    user, _ = request("POST", "/users/", {"name": "Demo resiliencia", "email": email, "password": "Demo-12345"})
    login, _ = request("POST", "/users/login", {"email": email, "password": "Demo-12345"})
    token = login["access_token"]
    card, _ = request("POST", "/cards/", {"user_id": user["id"]}, token)
    recharge = operation(amount="50.00")
    path = f'/cards/{card["id"]}'
    request("PATCH", path + "/recharge", recharge)
    stop = time.monotonic() + 12

    def worker(_):
        successes, retries, replicas = 0, 0, set()
        while time.monotonic() < stop:
            for attempt in range(3):
                try:
                    data, replica = request("GET", path, token=token)
                    if Decimal(str(data["balance"])) != 50:
                        raise AssertionError("Saldo inconsistente")
                    replicas.add(replica)
                    successes += 1
                    break
                except (OSError, RuntimeError):
                    retries += 1
                    if attempt == 2:
                        raise
                    time.sleep(0.25 * 2 ** attempt)
            time.sleep(0.1)
        return successes, retries, replicas

    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(worker, i) for i in range(6)]
            time.sleep(2)
            print("Deteniendo una réplica con carga en curso...", flush=True)
            compose("stop", "-t", "1", "web_replica_1")
            results = [future.result() for future in futures]
        print("Lecturas correctas:", sum(x[0] for x in results), "Reintentos:", sum(x[1] for x in results))
        print("Réplicas observadas:", sorted(set().union(*(x[2] for x in results))))
    finally:
        compose("start", "web_replica_1")
        # Nginx resuelve los nombres del upstream al arrancar.
        compose("restart", "lb")
        wait_ready()

    print("Reiniciando PostgreSQL y las APIs para demostrar durabilidad (interrupción planificada)...", flush=True)
    try:
        compose("stop", "web_replica_1", "web_replica_2", "web_replica_3")
        compose("restart", "db")
    finally:
        compose("up", "-d", "--wait")
        compose("restart", "lb")
        wait_ready()
    current, _ = request("GET", path, token=token)
    if Decimal(str(current["balance"])) != 50:
        raise AssertionError("El saldo no sobrevivió al reinicio")
    # Incluso después de reiniciar, repetir la recarga no duplica el crédito.
    request("PATCH", path + "/recharge", recharge)
    history, _ = request("GET", path + "/history", token=token)
    current, _ = request("GET", path, token=token)
    if len(history["history"]) != 1 or Decimal(str(current["balance"])) != 50:
        raise AssertionError("Idempotencia no persistida")
    print(f'OK: tarjeta {card["id"]}, saldo 50.00, un movimiento; token e idempotencia sobreviven a reinicios.')


if __name__ == "__main__":
    main()
