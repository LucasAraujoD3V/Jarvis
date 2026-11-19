@echo off
rem Abre o Jarvis com console (log na tela). Para uso normal sem console: pythonw jarvis.pyw
title Jarvis
cd /d "%~dp0"
python jarvis.pyw --console %*
