@echo off
rem Double-clic : met a jour la carte du jour et l'ouvre dans le navigateur.
rem La premiere fois : installe l'environnement et prepare le terrain (quelques minutes, ~300 Mo).
chcp 65001 >nul
cd /d "%~dp0"
title Champignons Radar

if not exist ".venv\Scripts\python.exe" (
    echo Premiere utilisation : installation de l'environnement Python...
    python -m venv .venv || goto :erreur
    ".venv\Scripts\python" -m pip install -q -r requirements.txt || goto :erreur
)

if not exist "data\processed\terrain_250m.npz" (
    echo Preparation du terrain : telechargement et pretraitement, une seule fois...
    ".venv\Scripts\python" -m radar fetch || goto :erreur
    ".venv\Scripts\python" -m radar terrain || goto :erreur
)

echo Mise a jour de la meteo et du score...
".venv\Scripts\python" -m radar score
set RC=%errorlevel%
if "%RC%"=="2" echo ATTENTION : un critere de coherence a echoue, voir le rapport ci-dessus.
if not "%RC%"=="0" if not "%RC%"=="2" goto :erreur

start "" "web\index.html"
ping -n 4 127.0.0.1 >nul
exit /b 0

:erreur
echo.
echo Une erreur s'est produite (message ci-dessus). Appuyez sur une touche pour fermer.
pause >nul
exit /b 1
