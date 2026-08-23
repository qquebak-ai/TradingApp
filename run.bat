@echo off
REM Запуск дашборда (Windows). Здесь же работает синхронизация с MT5.
chcp 65001 >nul
cd /d "%~dp0"

REM Без этой проверки Windows молча открывает Microsoft Store вместо Python.
python --version >nul 2>&1
if errorlevel 1 (
  echo.
  echo [!] Python не найден.
  echo     Скачай его с https://www.python.org/downloads/
  echo     При установке обязательно поставь галочку "Add python.exe to PATH".
  echo     Потом запусти этот файл заново.
  echo.
  pause
  exit /b 1
)

if not exist .venv (
  echo Первый запуск: устанавливаю зависимости, это займёт пару минут...
  python -m venv .venv
  if errorlevel 1 goto fail
  .venv\Scripts\python -m pip install --quiet --upgrade pip
  .venv\Scripts\pip install --quiet -r requirements.txt
  if errorlevel 1 goto fail
  echo Ставлю коннектор MetaTrader5...
  REM Ставится только на Windows; без него работает импорт отчёта.
  .venv\Scripts\pip install --quiet MetaTrader5
  echo Готово.
)

if "%TRADINGAPP_PORT%"=="" set TRADINGAPP_PORT=8420
if "%TRADINGAPP_HOST%"=="" set TRADINGAPP_HOST=127.0.0.1

echo.
echo ==========================================================
echo   Дашборд открыт: http://%TRADINGAPP_HOST%:%TRADINGAPP_PORT%
echo   Браузер откроется сам через несколько секунд.
echo   Это окно не закрывай — в нём работает приложение.
echo   Остановить: Ctrl+C или просто закрыть окно.
echo ==========================================================
echo.

REM Открываем браузер с задержкой, иначе он успевает раньше сервера.
start "" cmd /c "timeout /t 4 >nul && start http://%TRADINGAPP_HOST%:%TRADINGAPP_PORT%"
.venv\Scripts\python -m uvicorn backend.app.main:app --host %TRADINGAPP_HOST% --port %TRADINGAPP_PORT%
goto end

:fail
echo.
echo [!] Не удалось установить зависимости. Скопируй текст ошибки выше.
echo.
pause
exit /b 1

:end
pause
