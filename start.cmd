@echo off
setlocal
rem ============================================================
rem  Jymalaya AI Coach - one-command startup
rem  Starts: Docker Desktop -> InfluxDB container -> backend (uvicorn :8080)
rem          -> frontend (Vite :5173)
rem  Stop:   Ctrl+C / close the two windows, then: docker compose stop
rem ============================================================
cd /d "%~dp0"

if not exist ".env" (
    echo [!] .env not found in the repo root - the backend needs it ^(Garmin creds,
    echo     InfluxDB token, Anthropic key^). Create it, then re-run start.cmd.
    pause
    exit /b 1
)

echo [1/4] Checking the Docker engine...
docker info >nul 2>&1
if not errorlevel 1 goto docker_up

echo       Docker is not running - launching Docker Desktop...
set "DOCKER_DESKTOP=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
if not exist "%DOCKER_DESKTOP%" set "DOCKER_DESKTOP=%LOCALAPPDATA%\Docker\Docker Desktop.exe"
if not exist "%DOCKER_DESKTOP%" (
    echo [!] Docker Desktop not found - start it manually, then re-run start.cmd.
    pause
    exit /b 1
)
start "" "%DOCKER_DESKTOP%"

echo       Waiting for the Docker engine ...
set tries=0
:wait_docker
ping -n 4 127.0.0.1 >nul
docker info >nul 2>&1
if not errorlevel 1 goto docker_up
set /a tries+=1
if %tries% geq 40 (
    echo [!] Docker engine still not ready after 2 minutes - check Docker Desktop.
    pause
    exit /b 1
)
goto wait_docker

:docker_up
echo       Docker engine is ready.

echo [2/4] Starting the Jymalaya InfluxDB container...
docker compose up -d influxdb
if errorlevel 1 (
    echo [!] "docker compose up -d influxdb" failed - check: docker compose logs influxdb
    pause
    exit /b 1
)

echo       Waiting for InfluxDB on :8086 ...
set tries=0
:wait_influx
curl -s -o nul http://localhost:8086/health
if not errorlevel 1 goto influx_up
set /a tries+=1
if %tries% geq 30 (
    echo [!] InfluxDB did not come up after 60s - check: docker compose logs influxdb
    pause
    exit /b 1
)
ping -n 3 127.0.0.1 >nul
goto wait_influx
:influx_up
echo       InfluxDB is up.

echo [3/4] Starting backend (uvicorn :8080) in its own window...
start "Jymalaya backend (:8080)" cmd /k uvicorn main:app --reload --port 8080

if not exist "frontend\node_modules" (
    echo       Installing frontend dependencies - first run only...
    call npm.cmd install --prefix frontend
)

echo [4/4] Starting frontend (Vite :5173) in its own window...
start "Jymalaya frontend (:5173)" cmd /k npm.cmd run dev --prefix frontend

echo       Waiting for the dashboard on :5173 ...
set tries=0
:wait_vite
curl -s -o nul http://localhost:5173
if not errorlevel 1 goto open_browser
set /a tries+=1
if %tries% geq 15 (
    echo [!] Vite didn't answer after 30s - check the frontend window; opening browser anyway.
    goto open_browser
)
ping -n 3 127.0.0.1 >nul
goto wait_vite

:open_browser
start http://localhost:5173

echo.
echo  All started:
echo    Dashboard  http://localhost:5173
echo    API        http://localhost:8080  (docs: /docs, health: /health)
echo    InfluxDB   http://localhost:8086
echo.
echo  To stop: Ctrl+C (or close) the two server windows, then "docker compose stop".
endlocal
