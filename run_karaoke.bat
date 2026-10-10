@echo off
setlocal
cd /d "%~dp0"
set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    py -3 -m venv ".venv"
    if errorlevel 1 (
        echo Nao foi possivel configurar Python. Instale Python 3 e tente novamente.
        pause
        exit /b 1
    )
    "%PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Erro ao instalar dependencias. Confira internet e Python.
        pause
        exit /b 1
    )
)
"%PYTHON%" -m app.main
if errorlevel 1 pause
endlocal
