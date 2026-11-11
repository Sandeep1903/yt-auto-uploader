@echo off
title YouTube Auto Uploader - Background Folder Watcher
cd /d "%~dp0"
echo ========================================================
echo   YouTube Folder Watcher Active
echo   Drop any video file directly into this folder!
echo   Uploaded videos will automatically be marked [DONE]
echo   Press Ctrl+C to stop.
echo ========================================================
python watcher.py
pause
