@echo off
REM Агент для Windows: читает терминал MT5 и отправляет историю на твой VPS.
REM Настройки берутся из файла .env рядом с этим файлом (TRADINGAPP_URL, TRADINGAPP_TOKEN).
chcp 65001 >nul
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
  echo.
  echo [!] Python не найден. Скачай с https://www.python.org/downloads/
  echo     и обязательно отметь "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

if not exist .env (
  echo.
  echo [!] Нет файла .env — агент не знает, куда отправлять данные.
  echo     Скопируй .env.example в .env и впиши TRADINGAPP_URL и TRADINGAPP_TOKEN.
  echo.
  pause
  exit /b 1
)

if not exist .venv (
  echo Первый запуск: устанавливаю зависимости...
  python -m venv .venv
  if errorlevel 1 goto fail
  .venv\Scripts\python -m pip install --quiet --upgrade pip
  .venv\Scripts\pip install --quiet -r requirements.txt
  if errorlevel 1 goto fail
)

REM Коннектор к терминалу. Существует только под Windows.
.venv\Scripts\python -c "import MetaTrader5" >nul 2>&1
if errorlevel 1 (
  echo Ставлю коннектор MetaTrader5...
  .venv\Scripts\pip install --quiet MetaTrader5
  if errorlevel 1 goto fail
)

if "%SYNC_INTERVAL%"=="" set SYNC_INTERVAL=300

echo.
echo ==========================================================
echo   Агент запущен. Отправляю историю каждые %SYNC_INTERVAL% секунд.
echo   Терминал MT5 должен быть запущен и залогинен,
echo   а в его настройках включён "Алгоритмический трейдинг".
echo   Это окно не закрывай. Остановить: Ctrl+C.
echo ==========================================================
echo.

.venv\Scripts\python tools\mt5_agent.py --interval %SYNC_INTERVAL%
goto end

:fail
echo.
echo [!] Не удалось установить зависимости. Скопируй текст ошибки выше.
echo.
pause
exit /b 1

:end
pause
