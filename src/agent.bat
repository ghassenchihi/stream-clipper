@echo off
cd /d "%~dp0"
title Clipper agent - leave open (minimize it)
rem only one agent at a time: quit if another one beat in the last 25 s
powershell -NoProfile -Command "foreach($f in 'agent_alive.txt','busy_alive.txt'){ if((Test-Path $f) -and ((Get-Date)-(Get-Item $f).LastWriteTime).TotalSeconds -lt 25){exit 1} }; exit 0"
if errorlevel 1 exit /b
echo Clipper agent running. Claude starts jobs by itself.
python -m pip install --quiet --upgrade "yt-dlp[default]" deno faster-whisper av numpy static-ffmpeg > agent_setup_log.txt 2>&1
start "" /b python serve_clips.py
:loop
echo %date% %time% > agent_alive.txt
if exist GO_quit.txt (
  del GO_quit.txt
  exit /b
)
if exist GO_hunt.txt (
  del GO_hunt.txt
  del STOP 2>nul
  python -u clipper.py --hunt hunt.json > live_log.txt 2>&1
  echo EXIT CODE %errorlevel% >> live_log.txt
)
if exist GO_job.txt (
  del GO_job.txt
  python -u clipper.py --job job.json > job_log.txt 2>&1
  echo EXIT CODE %errorlevel% >> job_log.txt
)
if exist GO_update.txt (
  del GO_update.txt
  python -m pip install --quiet --upgrade "yt-dlp[default]" deno faster-whisper av numpy static-ffmpeg > agent_setup_log.txt 2>&1
)
timeout /t 10 /nobreak > nul
goto loop
