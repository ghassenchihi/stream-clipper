@echo off
cd /d "%~dp0"
echo Installing NVIDIA CUDA libraries for faster transcription (about 1 GB, one time only)...
python -m pip install --upgrade nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*" > gpu_install_log.txt 2>&1
echo EXIT CODE %errorlevel% >> gpu_install_log.txt
echo Done. Close this window.
pause
