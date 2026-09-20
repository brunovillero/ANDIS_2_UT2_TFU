$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not $env:BASE_URL) {
    $port = if ($env:PORT) { $env:PORT } else { "8080" }
    $env:BASE_URL = "http://localhost:$port"
}
docker compose up -d --build --wait
if ($LASTEXITCODE -ne 0) { throw "Falló el despliegue" }
python tests/test_api.py
if ($LASTEXITCODE -ne 0) { throw "Fallaron las pruebas de integración" }
python demo_resilience.py
if ($LASTEXITCODE -ne 0) { throw "Falló la demostración de resiliencia" }
