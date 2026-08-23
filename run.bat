@echo off
REM Запуск дашборда (Windows). Здесь же работает синхронизация с MT5.
cd /d "%~dp0"

if not exist .venv (
  echo Создаю окружение .venv...
  python -m venv .venv
  .venv\Scripts\pip install --quiet --upgrade pip
  .venv\Scripts\pip install --quiet -r requirements.txt
  echo Ставлю коннектор MetaTrader5...
  .venv\Scripts\pip install --quiet MetaTrader5
)

if "%TRADINGAPP_PORT%"=="" set TRADINGAPP_PORT=8420
if "%TRADINGAPP_HOST%"=="" set TRADINGAPP_HOST=127.0.0.1

echo Дашборд: http://%TRADINGAPP_HOST%:%TRADINGAPP_PORT%
start "" http://%TRADINGAPP_HOST%:%TRADINGAPP_PORT%
.venv\Scripts\python -m uvicorn backend.app.main:app --host %TRADINGAPP_HOST% --port %TRADINGAPP_PORT%
