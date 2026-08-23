#!/usr/bin/env sh
# Запуск дашборда (macOS / Linux).
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Создаю окружение .venv…"
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

PORT="${TRADINGAPP_PORT:-8420}"
HOST="${TRADINGAPP_HOST:-127.0.0.1}"
echo "Дашборд: http://${HOST}:${PORT}"
exec .venv/bin/python -m uvicorn backend.app.main:app --host "$HOST" --port "$PORT"
