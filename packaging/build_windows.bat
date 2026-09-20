@echo off
REM Builds the Windows version. Run this on Windows, not on Linux:
REM PyInstaller cannot build for a system other than the one it runs on.
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
echo Done: dist\MIAW-Marktplaats-Monitor-windows.zip
echo Unpack and start 'MIAW Marktplaats Monitor.exe'.
endlocal
