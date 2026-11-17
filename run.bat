@echo off
rem LAN Camera Viewer launcher - first run installs libraries automatically
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\TapoViewer\venv"
if exist "%VENV%\Scripts\pythonw.exe" goto check
echo Installing libraries for first run, please wait 2-3 minutes...
python -m venv "%VENV%"
if errorlevel 1 goto nopython
"%VENV%\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
goto config
:check
"%VENV%\Scripts\python.exe" -c "import av, PySide6, pytapo, websockets, cryptography" 2>nul
if errorlevel 1 (
  echo Installing new libraries...
  "%VENV%\Scripts\python.exe" -m pip install -r requirements.txt
)
:config
rem no config.json yet: the app opens its Settings window on first run
:start
start "" "%VENV%\Scripts\pythonw.exe" tapo_viewer.py
goto end
:nopython
echo Python not found. Install Python from python.org and tick "Add to PATH".
pause
goto end
:failed
echo Library install failed. Check your internet connection and try again.
rmdir /s /q "%VENV%"
pause
:end
