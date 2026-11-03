@echo off
rem Double-clic : ouvre la page locale du journal de sorties (fermer cette fenetre pour l'arreter).
chcp 65001 >nul
cd /d "%~dp0"
title Champignons Radar - Journal
if not exist ".venv\Scripts\python.exe" (
    echo Lancez d'abord Radar.bat une fois pour installer l'environnement.
    pause >nul
    exit /b 1
)
".venv\Scripts\python" -m radar journal
if errorlevel 1 (
    echo.
    echo Une erreur s'est produite. Appuyez sur une touche pour fermer.
    pause >nul
)
