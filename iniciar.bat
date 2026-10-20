@echo off
rem Inicia o Sistema RH ABMT no Windows (duplo clique).
cd /d "%~dp0"
where python >nul 2>nul || (
  echo Python nao encontrado.
  echo Instale em https://www.python.org/downloads/ e marque a opcao "Add python.exe to PATH".
  pause
  exit /b 1
)
if not exist .venv python -m venv .venv
.venv\Scripts\python -c "import flask" 2>nul || (
  echo Preparando o sistema pela primeira vez, aguarde...
  .venv\Scripts\python -m pip install -q -r requirements.txt || (pause & exit /b 1)
)
echo.
echo Sistema RH ABMT rodando em http://127.0.0.1:5000
echo Para encerrar, feche esta janela.
echo.
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:5000"
.venv\Scripts\python app.py
pause
