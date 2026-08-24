#!/usr/bin/env bash
# Открывает дашборд наружу: nginx + сертификат Let's Encrypt.
# Запускать от root ПОСЛЕ deploy/install.sh:
#
#   sudo bash deploy/setup-nginx.sh [почта-для-сертификата]
#
# Домен не нужен: используется sslip.io — имя <твой-ip>.sslip.io резолвится
# в этот же сервер, и сертификат на него выпускается как на обычный домен.
set -euo pipefail

APP_DIR=/opt/tradingapp
PORT=8420
EMAIL=""
LOAD_DEMO=0
for arg in "$@"; do
  case "$arg" in
    --demo) LOAD_DEMO=1 ;;
    *)      EMAIL="$arg" ;;
  esac
done

STEP="запуск"
step() { STEP="$1"; echo "==> $1"; }
on_error() {
  local code=$?
  echo >&2
  echo "======================================================" >&2
  echo " [!] Прервалось на шаге: $STEP (строка $1, код $code)" >&2
  echo "     Текст ошибки — выше. Скрипт можно запускать повторно." >&2
  echo "======================================================" >&2
  exit "$code"
}
trap 'on_error $LINENO' ERR

[ "$(id -u)" -eq 0 ] || { echo "Запусти от root: sudo bash deploy/setup-nginx.sh" >&2; exit 1; }

step "Проверяю, что приложение работает"
if ! systemctl is-active --quiet tradingapp 2>/dev/null; then
  echo "[!] Сервис tradingapp не запущен. Сначала: sudo bash deploy/install.sh" >&2
  exit 1
fi
curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null

step "Определяю адрес сервера"
IP="$(curl -fsS --max-time 8 https://api.ipify.org 2>/dev/null || true)"
[ -n "$IP" ] || IP="$(hostname -I | awk '{print $1}')"
case "$IP" in
  ''|*[!0-9.]*) echo "[!] Не удалось определить внешний IP. Задай вручную." >&2; exit 1 ;;
esac
HOST="$IP.sslip.io"
echo "    $HOST"

step "Проверяю, что имя резолвится в этот сервер"
RESOLVED="$(getent hosts "$HOST" | awk '{print $1}' | head -1 || true)"
if [ "$RESOLVED" != "$IP" ]; then
  echo "[!] $HOST резолвится в '$RESOLVED', а не в $IP." >&2
  echo "    Проверь DNS на сервере; без этого сертификат не выпустится." >&2
  exit 1
fi

step "Ставлю nginx и certbot, если их нет"
NEED=""
command -v nginx   >/dev/null || NEED="$NEED nginx"
command -v certbot >/dev/null || NEED="$NEED certbot python3-certbot-nginx"
if [ -n "$NEED" ]; then
  apt-get update -qq
  # shellcheck disable=SC2086
  apt-get install -y -qq $NEED
fi

step "Настраиваю nginx"
cp "$APP_DIR/deploy/nginx.conf.example" /etc/nginx/sites-available/tradingapp
sed -i "s/ИМЯ_ХОСТА/$HOST/" /etc/nginx/sites-available/tradingapp
ln -sf /etc/nginx/sites-available/tradingapp /etc/nginx/sites-enabled/tradingapp

# Проверка ДО перезагрузки: если конфиг плох, действующий сайт не пострадает.
if ! nginx -t; then
  echo "[!] nginx -t не прошёл. Убираю свой конфиг, чтобы не мешал." >&2
  rm -f /etc/nginx/sites-enabled/tradingapp
  exit 1
fi
systemctl reload nginx

step "Проверяю доступ по HTTP"
curl -fsS -H "Host: $HOST" "http://127.0.0.1/api/health" >/dev/null

step "Выпускаю сертификат"
CERT_OK=1
if [ -n "$EMAIL" ]; then
  certbot --nginx -d "$HOST" --agree-tos -m "$EMAIL" --redirect -n || CERT_OK=0
else
  certbot --nginx -d "$HOST" --agree-tos --register-unsafely-without-email --redirect -n || CERT_OK=0
fi

TOKEN="$(grep -E '^TRADINGAPP_TOKEN=' "$APP_DIR/.env" | cut -d= -f2- || true)"

if [ "$LOAD_DEMO" -eq 1 ]; then
  step "Загружаю демо-историю (260 сделок)"
  curl -fsS -H "Authorization: Bearer $TOKEN" \
    -F "file=@$APP_DIR/samples/demo_mt4_statement.html" \
    "http://127.0.0.1:$PORT/api/import/file" >/dev/null
  echo "    Загружено. Перед своей историей удали демо-данные командой из блока ниже."
fi

echo
echo "======================================================================"
if [ "$CERT_OK" -eq 1 ]; then
  SCHEME=https
  echo " Готово. Дашборд доступен по HTTPS."
else
  SCHEME=http
  echo " nginx настроен, но сертификат выпустить не удалось."
  echo " Частая причина — порт 80 закрыт извне: Let's Encrypt должен"
  echo " достучаться до него для проверки. Открой 80 и повтори:"
  echo "   sudo certbot --nginx -d $HOST --redirect"
  echo
  echo " ВНИМАНИЕ: пока сертификата нет, соединение не шифруется."
  echo " Не загружай реальную историю счёта до выпуска сертификата."
fi
echo
echo " Ссылка для входа (токен подставлен, вводить ничего не нужно):"
echo
echo "   $SCHEME://$HOST/?token=$TOKEN"
echo
echo " Открой её на телефоне. Браузер запомнит доступ, и токен"
echo " сразу исчезнет из адресной строки."
echo
if [ "$LOAD_DEMO" -eq 1 ]; then
  echo " Удалить демо-данные перед загрузкой своей истории:"
else
  echo " Посмотреть на демо-данных (260 сделок):"
  echo "   sudo bash $APP_DIR/deploy/setup-nginx.sh --demo"
  echo
  echo " Очистить базу:"
fi
echo "   curl -X DELETE -H 'Authorization: Bearer $TOKEN' \\"
echo "     'http://127.0.0.1:$PORT/api/trades?confirm=true'"
echo "======================================================================"
echo
