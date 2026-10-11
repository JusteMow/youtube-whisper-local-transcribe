@echo off
rem Usage : transcribe.bat <URL YouTube ou fichier> [-l fr] [-m turbo]
"%~dp0.venv\Scripts\python.exe" -I "%~dp0transcribe.py" %*
