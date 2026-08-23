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

if [ "$(id -u)" -ne 0 ]; then
  echo "Запусти от root: sudo bash deploy/install.sh" >&2
  exit 1
fi

echo "==> Проверяю зависимости системы"
if ! command -v python3 >/dev/null; then
  echo "Нет python3. Установи: apt install -y python3 python3-venv git" >&2
  exit 1
fi
if ! python3 -c "import venv" >/dev/null 2>&1; then
  echo "Нет модуля venv. Установи: apt install -y python3-venv" >&2
  exit 1
fi
command -v git >/dev/null || { echo "Нет git. Установи: apt install -y git" >&2; exit 1; }

echo "==> Пользователь $APP_USER"
id -u "$APP_USER" >/dev/null 2>&1 || \
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

echo "==> Код в $APP_DIR"
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

echo "==> Зависимости Python"
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
  echo "==> .env уже есть, не трогаю"
  TOKEN="$(grep -E '^TRADINGAPP_TOKEN=' "$APP_DIR/.env" | cut -d= -f2- || true)"
else
  echo "==> Создаю .env со случайным токеном"
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

echo "==> Автозапуск через systemd"
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
