@echo off
setlocal
title Stream Clipper - Setup
cd /d "%~dp0"
set "CLIPPER=%USERPROFILE%\Videos\clipper"

echo.
echo  ==========================================
echo     STREAM CLIPPER - one-click setup
echo  ==========================================
echo.

rem ---------- 1. Python 3.10+ ----------
echo [1/5] Checking Python...
call :findpy
if defined PY goto havepy
echo       Python not found. Installing Python 3.12 - takes a minute...
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
call :findpy
if defined PY goto havepy
echo.
echo  !! Could not install Python automatically.
echo     Install it from https://www.python.org/downloads/
echo     IMPORTANT: tick "Add python.exe to PATH" during install.
echo     Then double-click ClickMeFirst.bat again.
pause
exit /b 1
:havepy
echo       OK

rem ---------- 2. Clipper folder ----------
echo [2/5] Creating %CLIPPER% and copying the code...
if not exist "%CLIPPER%" mkdir "%CLIPPER%"
xcopy "src\*" "%CLIPPER%\" /Y /Q >nul
if errorlevel 1 (
  echo  !! Copy failed. Is the repo unzipped? Run ClickMeFirst.bat from the unzipped folder.
  pause
  exit /b 1
)
echo       OK

rem ---------- 3. Packages ----------
echo [3/5] Installing yt-dlp, faster-whisper, ffmpeg... - a few minutes the first time
python -m pip install --upgrade pip --quiet
python -m pip install --upgrade "yt-dlp[default]" deno faster-whisper av numpy static-ffmpeg
if errorlevel 1 (
  echo  !! Package install failed. Check your internet and run ClickMeFirst.bat again.
  pause
  exit /b 1
)
echo       OK

rem ---------- 4. Skill zip for the Claude app ----------
echo [4/5] Building the skill file for the Claude app...
powershell -NoProfile -Command "$t=Join-Path $env:TEMP 'stream-clipper'; Remove-Item $t -Recurse -Force -ErrorAction SilentlyContinue; New-Item -ItemType Directory $t | Out-Null; Copy-Item 'skill\SKILL.md' $t; Compress-Archive -Path $t -DestinationPath (Join-Path $env:CLIPPER 'stream-clipper-skill.zip') -Force"
copy /Y "START_HERE.txt" "%CLIPPER%\START_HERE.txt" >nul
echo       OK - %CLIPPER%\stream-clipper-skill.zip

rem ---------- 5. Background agent ----------
echo [5/5] Starting the clipper agent - hidden, starts with Windows...
start "" wscript "%CLIPPER%\SETUP_once.vbs"
echo       OK

echo.
echo  ==========================================
echo   DONE! Opening your clipper folder and the
echo   last 3 steps - upload skill, sign in, run.
echo  ==========================================
start "" explorer "%CLIPPER%"
start "" notepad "%CLIPPER%\START_HERE.txt"
timeout /t 8 >nul
exit /b 0

:findpy
set "PY="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
exit /b 0
