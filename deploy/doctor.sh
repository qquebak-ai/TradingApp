#!/usr/bin/env bash
# Проверяет всю цепочку и говорит, что именно сломано.
#   sudo bash deploy/doctor.sh
# Ничего не меняет — только смотрит.

APP_DIR=/opt/tradingapp
PORT=8420
OK="  [ок]  "
NO="  [!!]  "
problems=()

say()  { printf '%s%s\n' "$1" "$2"; }
fail() { say "$NO" "$1"; problems+=("$2"); }

echo
echo "======== ДИАГНОСТИКА TRADINGAPP ========"
echo

# 0. Есть ли вообще systemd — иначе все выводы про сервис будут враньём
if ! command -v systemctl >/dev/null || [ ! -d /run/systemd/system ]; then
  say "$NO" "systemd не запущен — проверки сервиса недостоверны"
  problems+=("это контейнер без systemd; приложение придётся запускать вручную")
fi

# 1. Сервис
if systemctl is-active --quiet tradingapp 2>/dev/null; then
  say "$OK" "Сервис tradingapp запущен"
elif systemctl list-unit-files 2>/dev/null | grep -q '^tradingapp.service'; then
  fail "Сервис установлен, но не запущен" "sudo systemctl start tradingapp && journalctl -u tradingapp -n 30"
else
  fail "Сервис tradingapp НЕ установлен" "sudo bash $APP_DIR/deploy/install.sh"
fi

# 2. Само приложение
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$PORT/api/health" 2>/dev/null)"
if [ "$CODE" = "200" ]; then
  say "$OK" "Приложение отвечает на 127.0.0.1:$PORT"
else
  fail "Приложение не отвечает на 127.0.0.1:$PORT (код '$CODE')" \
       "journalctl -u tradingapp -n 30"
fi

# 3. nginx
if command -v nginx >/dev/null; then
  if systemctl is-active --quiet nginx 2>/dev/null || pgrep -x nginx >/dev/null; then
    say "$OK" "nginx запущен"
  else
    fail "nginx установлен, но не запущен" "sudo systemctl start nginx"
  fi
  if [ -e /etc/nginx/sites-enabled/tradingapp ]; then
    say "$OK" "Конфиг сайта подключён"
    if nginx -t >/dev/null 2>&1; then
      say "$OK" "nginx -t проходит"
    else
      fail "nginx -t НЕ проходит" "sudo nginx -t"
    fi
  else
    fail "Конфиг сайта не подключён" "sudo bash $APP_DIR/deploy/setup-nginx.sh"
  fi
else
  fail "nginx не установлен" "sudo bash $APP_DIR/deploy/setup-nginx.sh"
fi

# 4. Имя хоста
IP="$(curl -fsS --max-time 8 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
HOST="$IP.sslip.io"
say "        " "Внешний адрес: $HOST"
RESOLVED="$(getent hosts "$HOST" 2>/dev/null | awk '{print $1}' | head -1)"
if [ "$RESOLVED" = "$IP" ]; then
  say "$OK" "Имя резолвится в этот сервер"
else
  fail "Имя резолвится в '$RESOLVED' вместо $IP" "проверь DNS на сервере (/etc/resolv.conf)"
fi

# 5. Сайт через nginx, изнутри
for scheme in http https; do
  port=80; [ "$scheme" = https ] && port=443
  CODE="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 6 \
          -H "Host: $HOST" "$scheme://127.0.0.1:$port/" 2>/dev/null)"
  if [ "$CODE" = "200" ]; then
    say "$OK" "Страница отдаётся по $scheme (изнутри сервера)"
  else
    say "        " "по $scheme изнутри: код '$CODE'"
  fi
done

# 6. Сертификат
if [ -d "/etc/letsencrypt/live/$HOST" ]; then
  say "$OK" "Сертификат для $HOST есть"
else
  fail "Сертификата для $HOST нет" "sudo certbot --nginx -d $HOST --redirect"
fi

# 7. Кто слушает наружу
echo
echo "  Слушают порты:"
ss -tln 2>/dev/null | awk '/:(80|443|8420) / {print "    " $1, $4}' | head -8 \
  || echo "    (ss недоступен)" 

# 8. Файрвол — самая частая причина «снаружи не грузится»
echo
if command -v ufw >/dev/null && ufw status 2>/dev/null | head -1 | grep -qi active; then
  if ufw status 2>/dev/null | grep -qE '(^|[^0-9])(80|443)($|[^0-9])|Nginx'; then
    say "$OK" "ufw включён, порты 80/443 разрешены"
  else
    fail "ufw включён и НЕ пропускает 80/443" "sudo ufw allow 80,443/tcp"
  fi
else
  say "        " "ufw выключен или не установлен — проверь файрвол в панели хостинга"
fi

echo
echo "========================================"
if [ ${#problems[@]} -eq 0 ]; then
  TOKEN="$(grep -E '^TRADINGAPP_TOKEN=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2-)"
  echo " Всё в порядке. Ссылка для входа:"
  echo
  echo "   https://$HOST/?token=$TOKEN"
else
  echo " Найдено проблем: ${#problems[@]}. Что сделать по порядку:"
  echo
  n=1
  for p in "${problems[@]}"; do
    echo "   $n) $p"
    n=$((n + 1))
  done
fi
echo "========================================"
echo
