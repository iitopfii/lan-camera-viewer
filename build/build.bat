@echo off
rem Rebuild LANCameraViewer.exe and the Setup installer
cd /d "%~dp0.."
set "PY=%LOCALAPPDATA%\TapoViewer\venv\Scripts\python.exe"
"%PY%" -m pip install -q pyinstaller pillow
"%PY%" build\make_icon.py
"%PY%" -m PyInstaller build\LANCameraViewer.spec --noconfirm --distpath dist --workpath "%LOCALAPPDATA%\TapoViewer\pyi-work"
if errorlevel 1 goto failed
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" /Q build\installer.iss
if errorlevel 1 goto failed
echo Done: dist\LANCameraViewer-Setup-1.1.0.exe
pause
goto end
:failed
echo Build failed.
pause
:end
