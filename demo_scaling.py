"""Demo de escalado manual 1 → 3. Conserva datos y restaura tres APIs al salir."""
from collections import Counter
from decimal import Decimal
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("BASE_URL", "http://localhost:" + os.environ.get("PORT", "8080"))
sys.path.insert(0, str(ROOT / "tests"))
from test_api import api, operation, ADMIN

DOCKER = shutil.which("docker") or str(Path.home() / ".docker/bin/docker")


def compose(*args, single=False, capture=False):
    env = os.environ.copy()
    env["NGINX_CONF"] = str(ROOT / ("nginx.single.conf" if single else "nginx.conf"))
    return subprocess.run([DOCKER, "compose", *args], cwd=ROOT, env=env,
                          check=True, text=True, capture_output=capture).stdout


def request(method, path, body=None, token=ADMIN):
    status, data, replica = api(method, path, body, token)
    if status not in (200, 201):
        raise RuntimeError(f"{method} {path}: HTTP {status}: {data}")
    return data, replica


def wait_ready():
    for _ in range(60):
        try:
            if api("GET", "/health")[0] == 200:
                return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError("La API no quedó disponible")


def verify_running(expected):
    services = set(compose("ps", "--services", "--status", "running", capture=True).split())
    apis = {name for name in services if name.startswith("web_replica_")}
    if apis != set(expected):
        raise AssertionError(f"APIs activas inesperadas: {apis}")
    print("APIs en ejecución:", ", ".join(sorted(apis)), flush=True)


def sample(path, token, expected, phase):
    counts = Counter()
    for _ in range(30):
        card, replica = request("GET", path, token=token)
        if Decimal(str(card["balance"])) != Decimal("75.00") or not replica:
            raise AssertionError("Saldo incorrecto o réplica no identificada")
        counts[replica] += 1
    if len(counts) != expected:
        raise AssertionError(f"Se esperaban {expected} réplicas: {dict(counts)}")
    print(f"{phase}: 30/30 lecturas correctas, saldo 75.00, mismo token.", flush=True)
    for replica, count in sorted(counts.items()):
        print(f"  Réplica {replica}: {count} solicitudes", flush=True)
    return set(counts)


def main():
    try:
        print("FASE 1 — Una réplica. Breve interrupción del proxy para preparar la demo.", flush=True)
        compose("stop", "lb", "web_replica_2", "web_replica_3")
        compose("up", "-d", "--wait", "web_replica_1")
        # Sin --no-deps, las dependencias del proxy arrancarían las tres APIs.
        compose("up", "-d", "--no-deps", "lb", single=True)
        wait_ready()
        verify_running(["web_replica_1"])
        email = str(uuid.uuid4()) + "@scaling.test"
        user, _ = request("POST", "/users/", {"name": "Demo escalado", "email": email, "password": "Demo-12345"}, None)
        login, _ = request("POST", "/users/login", {"email": email, "password": "Demo-12345"}, None)
        token = login["access_token"]
        card, _ = request("POST", "/cards/", {"user_id": user["id"]}, token)
        path = f'/cards/{card["id"]}'
        recharge = operation(amount="75.00")
        request("PATCH", path + "/recharge", recharge)
        original = sample(path, token, 1, "Antes de escalar")

        print("FASE 2 — Incorporar dos réplicas y configurar el balanceador para tres.", flush=True)
        compose("up", "-d", "--wait", "web_replica_2", "web_replica_3")
        # El cambio de archivo montado hace que Compose recree solamente el proxy.
        compose("up", "-d", "--no-deps", "lb")
        wait_ready()
        verify_running(["web_replica_1", "web_replica_2", "web_replica_3"])
        expanded = sample(path, token, 3, "Después de escalar")
        if not original.issubset(expanded):
            raise AssertionError("La réplica original no participó después del escalado")
        request("PATCH", path + "/recharge", recharge)
        history, _ = request("GET", path + "/history", token=token)
        current, _ = request("GET", path, token=token)
        if len(history["history"]) != 1 or Decimal(str(current["balance"])) != 75:
            raise AssertionError("Recarga duplicada después de escalar")
        print(f'OK: escalado 1 → 3, tarjeta {card["id"]}, token y saldo conservados; recarga sin duplicados.', flush=True)
    finally:
        print("Restaurando la configuración normal de tres réplicas...", flush=True)
        compose("up", "-d", "--wait")


if __name__ == "__main__":
    main()
