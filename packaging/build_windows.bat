@echo off
REM Bouwt de Windows-versie. Draai dit op Windows, niet op Linux:
REM PyInstaller kan niet voor een ander systeem bouwen dan waarop het draait.
setlocal
cd /d "%~dp0\.."

python -m venv .venv-build
.venv-build\Scripts\python -m pip install --upgrade pip
.venv-build\Scripts\python -m pip install -r requirements.txt pyinstaller

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
.venv-build\Scripts\python -m PyInstaller packaging\miaw.spec --noconfirm

powershell -NoProfile -Command ^
  "Compress-Archive -Path 'dist\MIAW Marktplaats Monitor' -DestinationPath 'dist\MIAW-Marktplaats-Monitor-windows.zip' -Force"

echo.
echo Klaar: dist\MIAW-Marktplaats-Monitor-windows.zip
echo Uitpakken en 'MIAW Marktplaats Monitor.exe' starten.
endlocal
