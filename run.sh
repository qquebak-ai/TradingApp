#!/usr/bin/env sh
# Запуск дашборда (macOS / Linux).
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[!] Python 3 не найден. Установи его с https://www.python.org/downloads/"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Первый запуск: устанавливаю зависимости, это займёт пару минут..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
  echo "Готово."
fi

PORT="${TRADINGAPP_PORT:-8420}"
HOST="${TRADINGAPP_HOST:-127.0.0.1}"

echo
echo "=========================================================="
echo "  Дашборд открыт: http://${HOST}:${PORT}"
echo "  Открой этот адрес в браузере."
echo "  Это окно не закрывай — в нём работает приложение."
echo "  Остановить: Ctrl+C"
echo "=========================================================="
echo

# Открываем браузер с задержкой, иначе он успевает раньше сервера.
( sleep 3; (open "http://${HOST}:${PORT}" || xdg-open "http://${HOST}:${PORT}") >/dev/null 2>&1 ) &

exec .venv/bin/python -m uvicorn backend.app.main:app --host "$HOST" --port "$PORT"
