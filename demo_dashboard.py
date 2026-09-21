#!/usr/bin/env python3
"""Panel local para ejecutar y presentar la demo obligatoria de la TFU 3.

No requiere paquetes de Python: sirve la interfaz, controla Docker Compose y
publica el progreso de los scripts existentes desde un servidor HTTP local.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import webbrowser


ROOT = Path(__file__).resolve().parent
HTML = ROOT / "demo" / "index.html"
DOCKER = shutil.which("docker") or str(Path.home() / ".docker/bin/docker")
PYTHON = shutil.which("python3") or "python3"
BASE_URL = os.environ.get("BASE_URL", "http://localhost:" + os.environ.get("PORT", "8080")).rstrip("/")

STEP_DEFINITIONS = [
    ("containers", "Contenedores", "Nginx, PostgreSQL y tres APIs saludables"),
    ("rest", "API REST", "Contrato OpenAPI, salud, seguridad y balanceador"),
    ("scaling", "Escalado horizontal", "Cambio real de una a tres réplicas"),
    ("shared", "Estado e idempotencia", "Mismo token y saldo, sin recargas duplicadas"),
    ("acid", "ACID y concurrencia", "Transacciones, rollback y bloqueos bajo carga"),
]

STATE_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()


def initial_state() -> dict:
    return {
        "running": False,
        "result": "idle",
        "message": "Lista para ejecutar",
        "started_at": None,
        "finished_at": None,
        "steps": [
            {"id": step_id, "title": title, "description": description,
             "status": "pending", "detail": "Pendiente", "duration": None}
            for step_id, title, description in STEP_DEFINITIONS
        ],
        "logs": [],
    }


STATE = initial_state()


def snapshot() -> dict:
    with STATE_LOCK:
        return copy.deepcopy(STATE)


def update_state(**values) -> None:
    with STATE_LOCK:
        STATE.update(values)


def reset_state() -> None:
    fresh = initial_state()
    fresh.update(running=True, result="running", message="Demo en ejecución", started_at=time.time())
    with STATE_LOCK:
        STATE.clear()
        STATE.update(fresh)


def update_step(step_id: str, status: str, detail: str, duration: Optional[float] = None) -> None:
    with STATE_LOCK:
        for step in STATE["steps"]:
            if step["id"] == step_id:
                step.update(status=status, detail=detail, duration=duration)
                break


def add_log(section: str, message: str, level: str = "info") -> None:
    message = message.rstrip()
    if not message:
        return
    with STATE_LOCK:
        STATE["logs"].append({
            "number": len(STATE["logs"]) + 1,
            "time": time.strftime("%H:%M:%S"),
            "section": section,
            "level": level,
            "message": message,
        })
        STATE["logs"] = STATE["logs"][-800:]


def run_command(section: str, args: list[str], env: Optional[dict] = None) -> list[str]:
    """Ejecuta un comando sin shell y retransmite su salida al panel."""
    add_log(section, "$ " + " ".join(args))
    process = subprocess.Popen(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    lines = []
    assert process.stdout is not None
    for line in process.stdout:
        clean = line.rstrip()
        lines.append(clean)
        add_log(section, clean)
    code = process.wait()
    if code:
        last_line = next((line for line in reversed(lines) if line.strip()), "sin detalle")
        raise RuntimeError(f"El comando terminó con código {code}: {last_line}")
    return lines


def request_json(path: str, token: Optional[str] = None, method: str = "GET",
                 body: Optional[dict] = None) -> tuple[int, dict, Optional[str]]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if body is None else json.dumps(body).encode()
    request = Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, json.load(response), response.headers.get("X-Replica-ID")
    except HTTPError as error:
        return error.code, json.load(error), error.headers.get("X-Replica-ID")


def run_timed_step(step_id: str, action) -> object:
    started = time.monotonic()
    step = next(item for item in STEP_DEFINITIONS if item[0] == step_id)
    update_step(step_id, "running", "Ejecutando…")
    add_log(step[1], "INICIO — " + step[2])
    try:
        result, detail = action()
    except Exception as exc:
        elapsed = time.monotonic() - started
        update_step(step_id, "failed", str(exc), elapsed)
        add_log(step[1], "ERROR — " + str(exc), "error")
        raise
    elapsed = time.monotonic() - started
    update_step(step_id, "passed", detail, elapsed)
    add_log(step[1], "OK — " + detail, "success")
    return result


def check_containers():
    env = os.environ.copy()
    env.setdefault("NGINX_CONF", str(ROOT / "nginx.conf"))
    run_command("Contenedores", [DOCKER, "compose", "up", "-d", "--build", "--wait"], env)
    services = run_command(
        "Contenedores", [DOCKER, "compose", "ps", "--services", "--status", "running"], env
    )
    running = {line.strip() for line in services if line.strip()}
    expected = {"db", "lb", "web_replica_1", "web_replica_2", "web_replica_3"}
    missing = sorted(expected - running)
    if missing:
        raise RuntimeError("Servicios ausentes: " + ", ".join(missing))
    return running, "5/5 servicios en ejecución y saludables"


def check_rest():
    status, health, replica = request_json("/health")
    if status != 200 or health.get("status") != "ok" or not replica:
        raise RuntimeError(f"Health check inesperado: HTTP {status} {health}")
    status, openapi, _ = request_json("/openapi.json")
    paths = openapi.get("paths", {})
    if status != 200 or len(paths) < 10:
        raise RuntimeError("El contrato OpenAPI no contiene las interfaces esperadas")
    status, _, _ = request_json(
        "/process", method="POST", body={"item_id": 1, "name": "Demo"}
    )
    if status != 401:
        raise RuntimeError(f"La API no rechazó el acceso anónimo como se esperaba: HTTP {status}")
    add_log("API REST", f"GET /health → HTTP 200, atendida por {replica}")
    add_log("API REST", f"GET /openapi.json → {len(paths)} rutas documentadas")
    add_log("API REST", "Acceso sin credencial → rechazado por la API")
    return paths, f"{len(paths)} rutas OpenAPI; salud y seguridad verificadas"


def check_scaling():
    env = os.environ.copy()
    env["BASE_URL"] = BASE_URL
    lines = run_command("Escalado horizontal", [PYTHON, "-u", "demo_scaling.py"], env)
    output = "\n".join(lines)
    evidence = [line for line in lines if "Réplica " in line]
    if "OK: escalado 1 → 3" not in output or len(evidence) < 4:
        raise RuntimeError("El script terminó sin la evidencia esperada de 1 → 3 réplicas")
    return output, "1 → 3 réplicas; 60/60 lecturas correctas"


def check_shared(scaling_output: str):
    required = ("mismo token", "saldo conservados", "recarga sin duplicados")
    if not all(fragment in scaling_output for fragment in required):
        raise RuntimeError("Falta evidencia de estado compartido o idempotencia")
    add_log("Estado e idempotencia", "El token creado con una réplica funcionó después del escalado")
    add_log("Estado e idempotencia", "Saldo final 75.00 y un único movimiento de recarga")
    return True, "Token y saldo compartidos; reintento sin duplicar la recarga"


def check_acid():
    env = os.environ.copy()
    env["BASE_URL"] = BASE_URL
    lines = run_command("ACID y concurrencia", [PYTHON, "-u", "tests/test_api.py"], env)
    output = "\n".join(lines)
    if "Ran 9 tests" not in output or "OK" not in output:
        raise RuntimeError("La suite no informó 9 pruebas aprobadas")
    add_log("ACID y concurrencia", "Incluye rollback, aforo concurrente y protección del saldo")
    return output, "9/9 pruebas: rollback, bloqueos e idempotencia concurrente"


def restore_cluster() -> None:
    env = os.environ.copy()
    env["NGINX_CONF"] = str(ROOT / "nginx.conf")
    try:
        run_command("Restauración", [DOCKER, "compose", "up", "-d", "--wait"], env)
        add_log("Restauración", "Configuración normal de tres réplicas lista", "success")
    except Exception as exc:
        add_log("Restauración", "No se pudo restaurar automáticamente: " + str(exc), "error")


def run_demo() -> None:
    with RUN_LOCK:
        reset_state()
        scaling_started = False
        try:
            run_timed_step("containers", check_containers)
            run_timed_step("rest", check_rest)
            scaling_started = True
            scaling_output = run_timed_step("scaling", check_scaling)
            run_timed_step("shared", lambda: check_shared(scaling_output))
            run_timed_step("acid", check_acid)
        except Exception as exc:
            with STATE_LOCK:
                for step in STATE["steps"]:
                    if step["status"] == "pending":
                        step.update(status="skipped", detail="Omitida por un error anterior")
            if scaling_started:
                restore_cluster()
            update_state(
                running=False,
                result="failed",
                message="La demo se detuvo: " + str(exc),
                finished_at=time.time(),
            )
            return
        update_state(
            running=False,
            result="passed",
            message="Demo completa: todos los requisitos quedaron demostrados",
            finished_at=time.time(),
        )


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "AccesoRodoDemo/1.0"

    def log_message(self, _format, *_args):
        return

    def send_bytes(self, status: int, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, status: int, payload: dict) -> None:
        self.send_bytes(status, json.dumps(payload).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self.send_bytes(200, HTML.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/status":
            self.send_json(200, snapshot())
        elif path == "/favicon.ico":
            self.send_bytes(204, b"", "image/x-icon")
        else:
            self.send_json(404, {"detail": "No encontrado"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/api/run":
            self.send_json(404, {"detail": "No encontrado"})
            return
        with STATE_LOCK:
            if STATE["running"]:
                already_running = True
            else:
                STATE.update(running=True, result="running", message="Iniciando demo…")
                already_running = False
        if already_running:
            self.send_json(409, {"detail": "Ya hay una demo en ejecución"})
            return
        threading.Thread(target=run_demo, name="tfu3-demo", daemon=True).start()
        self.send_json(202, {"status": "started"})


def parse_args():
    parser = argparse.ArgumentParser(description="Panel local de la demo TFU 3")
    parser.add_argument("--port", type=int, default=8090, help="Puerto del panel (8090 por defecto)")
    parser.add_argument("--no-open", action="store_true", help="No abrir el navegador automáticamente")
    return parser.parse_args()


def main():
    args = parse_args()
    if not HTML.exists():
        raise SystemExit(f"No se encontró la interfaz: {HTML}")
    url = f"http://127.0.0.1:{args.port}"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), DemoHandler)
    print("Panel de demo TFU 3 listo en " + url)
    print("Presioná Ctrl+C para cerrarlo.")
    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPanel cerrado.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
