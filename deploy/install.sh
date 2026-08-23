#!/usr/bin/env bash
# Установка TradingApp на Linux-VPS. Запускать от root:
#
#   sudo bash deploy/install.sh
#
# Скрипт создаёт отдельного пользователя, ставит зависимости, генерирует токен
# и включает автозапуск. Он НЕ трогает твой nginx и НЕ открывает порты наружу —
# что делать дальше, напечатает в конце.
set -euo pipefail

APP_DIR=/opt/tradingapp
APP_USER=tradingapp
REPO=https://github.com/qquebak-ai/TradingApp.git
BRANCH=claude/trading-app-statistics-5r7nsg

# `set -e` aborts on the first failure, and the reason scrolls away in a long
# install. The trap names the step that died so it is the last thing on screen.
STEP="запуск"
step() { STEP="$1"; echo "==> $1"; }
on_error() {
  local code=$?
  echo >&2
  echo "======================================================" >&2
  echo " [!] Установка прервалась на шаге: $STEP" >&2
  echo "     Строка $1, код выхода $code." >&2
  echo "     Текст ошибки — чуть выше этого блока." >&2
  echo "     Скрипт можно запускать повторно: он продолжит с места обрыва." >&2
  echo "======================================================" >&2
  exit "$code"
}
trap 'on_error $LINENO' ERR

if [ "$(id -u)" -ne 0 ]; then
  echo "Запусти от root: sudo bash deploy/install.sh" >&2
  exit 1
fi

step "Проверяю зависимости системы"
missing=""
command -v python3 >/dev/null || missing="$missing python3"
command -v git     >/dev/null || missing="$missing git"

# `import venv` succeeds on Debian/Ubuntu even when python3-venv is absent —
# the module is there, but `python3 -m venv` then dies on ensurepip. Checking
# ensurepip is what actually tells the two apart.
python3 -c "import ensurepip" >/dev/null 2>&1 || missing="$missing python3-venv"

if [ -n "$missing" ]; then
  echo "    Не хватает пакетов:$missing"
  if command -v apt-get >/dev/null; then
    echo "    Ставлю их..."
    apt-get update -qq
    # shellcheck disable=SC2086
    apt-get install -y -qq $missing
  else
    echo "[!] Установи их вручную и запусти скрипт заново:$missing" >&2
    exit 1
  fi
fi

if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  echo "[!] python3-venv так и не поставился. Установи вручную:" >&2
  echo "    sudo apt install -y python3-venv" >&2
  exit 1
fi

# systemd is what runs the service; without it the last step cannot work.
if ! command -v systemctl >/dev/null || [ ! -d /run/systemd/system ]; then
  echo "[!] systemd не обнаружен. Это контейнер (LXC/Docker) без systemd?" >&2
  echo "    Приложение можно запустить вручную:" >&2
  echo "    cd $APP_DIR && sudo -u $APP_USER .venv/bin/python -m uvicorn \\" >&2
  echo "      backend.app.main:app --host 127.0.0.1 --port 8420" >&2
  exit 1
fi

step "Пользователь $APP_USER"
id -u "$APP_USER" >/dev/null 2>&1 || \
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

step "Код в $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  sudo -u "$APP_USER" git -C "$APP_DIR" fetch origin "$BRANCH"
  sudo -u "$APP_USER" git -C "$APP_DIR" checkout "$BRANCH"
  sudo -u "$APP_USER" git -C "$APP_DIR" pull origin "$BRANCH"
else
  mkdir -p "$APP_DIR"
  chown "$APP_USER:$APP_USER" "$APP_DIR"
  sudo -u "$APP_USER" git clone --branch "$BRANCH" "$REPO" "$APP_DIR"
fi
sudo -u "$APP_USER" mkdir -p "$APP_DIR/data"

step "Зависимости Python"
[ -d "$APP_DIR/.venv" ] || sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"

# sudo сбрасывает окружение, поэтому настройки прокси и сертификатов, если они
# есть у root, надо передать явно — иначе pip упрётся в стену таймаутов.
pip_as_app() {
  sudo -u "$APP_USER" env \
    ${HTTPS_PROXY:+HTTPS_PROXY="$HTTPS_PROXY"} \
    ${HTTP_PROXY:+HTTP_PROXY="$HTTP_PROXY"} \
    ${NO_PROXY:+NO_PROXY="$NO_PROXY"} \
    ${PIP_CERT:+PIP_CERT="$PIP_CERT"} \
    ${PIP_INDEX_URL:+PIP_INDEX_URL="$PIP_INDEX_URL"} \
    "$APP_DIR/.venv/bin/pip" "$@"
}

if ! pip_as_app install --quiet --upgrade pip; then
  echo "[!] pip не смог выйти в интернет. Проверь DNS и доступ к pypi.org." >&2
  exit 1
fi
pip_as_app install --quiet -r "$APP_DIR/requirements.txt"

# Токен генерируется один раз и при повторном запуске не перезаписывается,
# иначе телефон разлогинится на каждом обновлении.
if [ -f "$APP_DIR/.env" ]; then
  step ".env уже есть, не трогаю"
  TOKEN="$(grep -E '^TRADINGAPP_TOKEN=' "$APP_DIR/.env" | cut -d= -f2- || true)"
else
  step "Создаю .env со случайным токеном"
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  cat > "$APP_DIR/.env" <<ENV
TRADINGAPP_DB=$APP_DIR/data/trading.db
# Наружу смотрит только reverse proxy; сам порт в интернет не открываем.
TRADINGAPP_HOST=127.0.0.1
TRADINGAPP_PORT=8420
TRADINGAPP_TOKEN=$TOKEN
ENV
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
fi
chmod 600 "$APP_DIR/.env"

step "Автозапуск через systemd"
cp "$APP_DIR/deploy/tradingapp.service" /etc/systemd/system/tradingapp.service
systemctl daemon-reload
systemctl enable --now tradingapp
sleep 2

if ! systemctl is-active --quiet tradingapp; then
  echo "[!] Сервис не поднялся. Смотри: journalctl -u tradingapp -n 50" >&2
  exit 1
fi

IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
HOSTNAME_SUGGESTION="${IP}.sslip.io"

cat <<DONE

================================================================
 Готово. Сервис работает на 127.0.0.1:8420 (наружу пока закрыт).

 Токен доступа:
   $TOKEN

 Осталось настроить nginx и сертификат:

   sudo cp $APP_DIR/deploy/nginx.conf.example /etc/nginx/sites-available/tradingapp
   sudo sed -i 's/ИМЯ_ХОСТА/$HOSTNAME_SUGGESTION/' /etc/nginx/sites-available/tradingapp
   sudo ln -s /etc/nginx/sites-available/tradingapp /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   sudo certbot --nginx -d $HOSTNAME_SUGGESTION

 Потом открой с телефона:
   https://$HOSTNAME_SUGGESTION

 Логи:            journalctl -u tradingapp -f
 Перезапуск:      sudo systemctl restart tradingapp
================================================================

DONE
