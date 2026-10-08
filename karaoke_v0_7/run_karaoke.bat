@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0..\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Ambiente virtual compartilhado nao encontrado.
    echo Caminho esperado:
    echo %~dp0..\.venv\Scripts\python.exe
    echo.
    echo Crie o ambiente na pasta raiz karaoke e instale as dependencias com:
    echo ..\.venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

"%PYTHON%" -m app.main

if errorlevel 1 pause
endlocal
