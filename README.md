# Acceso Rodó — TFU 3

API REST de tarjetas prepagas y control de acceso a atracciones. Evoluciona la infraestructura de la TFU 2 hacia el dominio de la TFU 1. Es un monolito modular con tres réplicas sin estado local, balanceadas por Nginx, y una base PostgreSQL compartida.

## Ejecución

Requisitos: Docker Desktop iniciado (incluye Compose). Los scripts externos requieren Python 3.9+; no hay que instalar paquetes Python en el host.

```bash
docker compose up -d --build --wait
```

API: http://localhost:8080 · Swagger: http://localhost:8080/docs · Salud: http://localhost:8080/health.

El puerto cambió de 80 (TFU 2) a **8080**, enlazado solamente a localhost. Si Docker no se reconoce en macOS: `export PATH="$HOME/.docker/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"`.

```bash
# Demo completa: integración, concurrencia, caída de nodo y persistencia.
bash demo_script.sh
# Windows:
# .\demo_script.ps1

# Solo las pruebas (sin reiniciar servicios):
python3 tests/test_api.py
# Solo la demo de caída/reinicio:
python3 demo_resilience.py
# Cliente original de TFU 2:
python3 client_test.py
```

La demo completa detiene temporalmente una réplica, la restaura y después reinicia la base y las APIs para verificar durabilidad. Esa segunda fase provoca una interrupción planificada; no es una prueba de alta disponibilidad de PostgreSQL. Cada ejecución crea datos nuevos identificados con UUID y no borra datos existentes.

## Recorrido manual con curl

Los ejemplos suponen IDs 1; reemplazarlos por los IDs devueltos si la base ya contiene datos. Usar nuevos `operation_id` para nuevas operaciones; repetir el mismo ID y contenido para reintentar una operación anterior.

```bash
# 1. Crear usuario.
curl -sS http://localhost:8080/users/ -H 'Content-Type: application/json' \
  -d '{"name":"Visitante demo","email":"visitante@example.com","password":"Demo-12345"}'

# 2. Iniciar sesión. El access_token permite consultar las tarjetas del usuario.
curl -sS http://localhost:8080/users/login -H 'Content-Type: application/json' \
  -d '{"email":"visitante@example.com","password":"Demo-12345"}'

# Para simplificar el recorrido restante se usa la credencial administrativa de demo.
export ADMIN_TOKEN="${ADMIN_TOKEN:-TFU-UT2-SECRET-KEY}"

# 3. Crear y recargar tarjeta.
curl -sS http://localhost:8080/cards/ -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"user_id":1}'
curl -sS -X PATCH http://localhost:8080/cards/1/recharge -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"amount":"100.00","operation_id":"a49edb3f-e35d-4ca1-9694-c3b4b5fd2a01"}'

# 4. Crear atracción.
curl -sS http://localhost:8080/rides/ -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"name":"Rueda gigante","price":"25.00","capacity":2}'

# 5. Acceso: cobra 25, ocupa un lugar y registra el movimiento.
curl -i http://localhost:8080/access/validate -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"card_id":1,"ride_id":1,"operation_id":"a49edb3f-e35d-4ca1-9694-c3b4b5fd2a02"}'

# 6. Consultar saldo e historial. Repetir el paso 5 no duplica el cobro.
curl -i http://localhost:8080/cards/1 -H "Authorization: Bearer $ADMIN_TOKEN"
curl -sS http://localhost:8080/cards/1/history -H "Authorization: Bearer $ADMIN_TOKEN"

# 7. Finalizar ronda.
curl -sS http://localhost:8080/rides/1/finish-round -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"operation_id":"a49edb3f-e35d-4ca1-9694-c3b4b5fd2a03"}'
```

Todas las respuestas incluyen `X-Replica-ID`. Varias consultas consecutivas permiten observar el balanceo. Los importes pueden aparecer como números JSON o cadenas decimales; el almacenamiento y cálculo son `NUMERIC(12,2)`/`Decimal`, no `float`.

## Contratos y permisos

Swagger `/docs` contiene los esquemas completos, campos obligatorios y respuestas de validación.

| Operación | Credencial |
|---|---|
| `POST /users/`, `POST /users/login` | Pública |
| `DELETE /users/{id}` | Propio usuario o administrador |
| `POST /cards/`, `GET /cards/{id}`, `GET /cards/{id}/history`, `DELETE /cards/{id}` | Propietario o administrador |
| `PATCH /cards/{id}/recharge` | Administrador/cajero de demo |
| `GET /rides/{id}` | Usuario autenticado |
| `POST /rides/`, `PATCH /rides/{id}`, `PATCH /rides/{id}/status`, `DELETE /rides/{id}` | Administrador/operador de demo |
| `POST /rides/{id}/finish-round`, `POST /access/validate` | Administrador/operador de demo |

Recarga, acceso y fin de ronda exigen `operation_id` UUID. La clave se comparte entre tipos de operación para un mismo actor: cambiar el contenido o el tipo usando la misma clave devuelve 409. Se conserva la respuesta original, que puede diferir del estado actual si hubo operaciones posteriores. Solo se registran operaciones confirmadas; una rechazada puede reintentarse después de corregir su causa.

Errores principales: 401 sin identidad válida, 403 por permisos o reglas de negocio, 404 recurso inexistente, 409 conflicto, 422 entrada inválida, 503 indisponibilidad de base. Solo reintentar errores transitorios; conservar el UUID cuando el resultado de una escritura es incierto. Las altas de usuario/tarjeta/atracción no implementan idempotencia automática.

## Configuración y operación

Defaults de desarrollo: `ADMIN_TOKEN=TFU-UT2-SECRET-KEY`, `POSTGRES_PASSWORD=demo`, `TOKEN_SECRET=solo-demo-cambiar-antes-de-publicar`, `PORT=8080`. Son credenciales públicas de una demo local, sin TLS ni integración de pagos reales. Todas las réplicas reciben las mismas variables. El token de usuario dura una hora y la baja del usuario invalida su uso. La API conserva el bloqueo de login de cinco intentos durante quince minutos en PostgreSQL.

Para personalizar, exportar variables antes del arranque. Los scripts también aceptan `BASE_URL` y `ADMIN_TOKEN`. Si se usa un `.env` de Compose, exportar además los valores relevantes para los scripts Python. Cambiar `POSTGRES_PASSWORD` no cambia la contraseña de una base ya inicializada; requiere administrarla en PostgreSQL.

```bash
docker compose ps
docker compose logs --tail=100
docker compose down  # Conserva el volumen y los datos.
```

Nginx resuelve los nombres de las réplicas al arrancar: después de recrearlas, ejecutar `docker compose restart lb`. Para esta demo el número de réplicas está declarado explícitamente en Compose y Nginx. No hay autoescalado.

## Archivos

- `TFU3.md`: informe arquitectónico y trazabilidad con las TFU anteriores.
- `docs/components.puml`: modelo UML de componentes e interfaces.
- `app/`: API, componentes, autenticación, idempotencia y persistencia.
- `tests/test_api.py`: pruebas REST de seguridad, transacciones y concurrencia.
- `demo_resilience.py`: caída de réplica y reinicio con conservación de estado.
- `docs/validacion.md`: resultados observados de la ejecución local.
