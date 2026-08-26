@echo off
setlocal enabledelayedexpansion

set REPO_URL=https://github.com/Predators972/passenger-counting-supervision.git
set PROJECT_DIR=%~dp0passenger-counting-supervision

set PYTHON_VERSION=3.12.4
set PYTHON_INSTALLER_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/python-%PYTHON_VERSION%-amd64.exe
set PYTHON_INSTALL_DIR=%LOCALAPPDATA%\Programs\Python\Python312

set GIT_VERSION=2.45.2
set GIT_INSTALLER_URL=https://github.com/git-for-windows/git/releases/download/v%GIT_VERSION%.windows.1/Git-%GIT_VERSION%-64-bit.exe
set GIT_INSTALL_DIR=%LOCALAPPDATA%\Programs\Git

set PATH=%PYTHON_INSTALL_DIR%;%PYTHON_INSTALL_DIR%\Scripts;%GIT_INSTALL_DIR%\cmd;%PATH%

if not exist "%PYTHON_INSTALL_DIR%\python.exe" (
    echo Python absent, installation en cours...
    curl -L --ssl-no-revoke -o "%TEMP%\python-installer.exe" "%PYTHON_INSTALLER_URL%"
    "%TEMP%\python-installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=0
    del "%TEMP%\python-installer.exe"
)

if not exist "%GIT_INSTALL_DIR%\cmd\git.exe" (
    echo Git absent, installation en cours...
    curl -L --ssl-no-revoke -o "%TEMP%\git-installer.exe" "%GIT_INSTALLER_URL%"
    "%TEMP%\git-installer.exe" /VERYSILENT /NORESTART /CURRENTUSER
    del "%TEMP%\git-installer.exe"
)

if not exist "%PYTHON_INSTALL_DIR%\python.exe" (
    echo.
    echo Echec de l'installation de Python. Verifiez la connexion reseau.
    pause
    exit /b 1
)

if not exist "%GIT_INSTALL_DIR%\cmd\git.exe" (
    echo.
    echo Echec de l'installation de Git. Verifiez la connexion reseau.
    pause
    exit /b 1
)

if not exist "%PROJECT_DIR%" (
    echo Premiere installation, recuperation du projet...
    git clone "%REPO_URL%" "%PROJECT_DIR%"
    if errorlevel 1 (
        echo.
        echo Echec de la recuperation du projet. Verifiez la connexion reseau.
        pause
        exit /b 1
    )
) else (
    echo Mise a jour du projet...
    pushd "%PROJECT_DIR%"
    git pull
    popd
)

echo Copie de la cle de dechiffrement...
copy /Y "%~dp0credentials.key" "%PROJECT_DIR%\backend\credentials.key" >nul

set VENV_DIR=%PROJECT_DIR%\backend\venv

if not exist "%VENV_DIR%" (
    echo Premier lancement, installation des dependances...
    pushd "%PROJECT_DIR%\backend"
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Echec de l'installation. Verifiez la connexion reseau.
        popd
        pause
        exit /b 1
    )
    popd
)

echo Demarrage de l'outil...
set RUN_SCRIPT=%TEMP%\run_supervision.bat
(
    echo @echo off
    echo cd /d "%PROJECT_DIR%\backend"
    echo call venv\Scripts\activate.bat
    echo uvicorn app.main:app
) > "%RUN_SCRIPT%"

start "Supervision comptage voyageurs" cmd /k "%RUN_SCRIPT%"

timeout /t 4 /nobreak >nul
start "" http://127.0.0.1:8000

endlocal
