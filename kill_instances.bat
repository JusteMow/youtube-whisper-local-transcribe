@echo off
REM Port : synchroniser avec le defaut de server.py (--port).
REM =====================================================
REM KILL INSTANCES - nettoyage seul, sans relance. Utile quand une instance
REM traine (fenetre fermee brutalement, serveur fige). lancer.bat integre deja
REM ce nettoyage : ce script sert au diagnostic manuel.
REM
REM Pipeline :
REM +- mode standalone (defaut) : PORTS ci-dessous, messages [SCAN], pause
REM \- mode internal (1er arg) : ports en args suivants, sans pause - appele
REM    par lancer.bat (un seul mecanisme de kill)
REM !: for %%P dans un bloc () echoue sous cmd /c depuis PowerShell 7 (%%P -> %P,
REM    kill inoperant) ; chaque port passe par call :kill_one_port
REM    (args positionnels %PORT%, pas de boucle imbriquee).
REM =====================================================

setlocal EnableDelayedExpansion

if /i "%~1"=="internal" goto :kill_internal

REM ----------------- CONFIGURATION ---------------------
REM 8765 = interface web (server.py). Un autre port passe par lancer.bat --port N,
REM qui nettoie lui-meme ce port.
set "PORTS=8765"
REM -----------------------------------------------------

set "FOUND=0"
for %%P in (%PORTS%) do call :kill_one_port %%P verbose

if "!FOUND!"=="0" (
    echo [OK] Aucun processus a arreter
    pause
    exit /b 0
)

echo [VERIFY] Verification finale...
set "REMAINING=0"
for %%P in (%PORTS%) do call :check_port %%P
if "!REMAINING!"=="0" (
    echo [SUCCESS] Tous les processus ont ete arretes
) else (
    echo [WARNING] Certains processus sont encore actifs - relancer, ou inspecter
    echo           netstat -ano pour un processus protege.
)
pause
exit /b 0

:kill_internal
shift
:kill_internal_loop
if "%~1"=="" exit /b 0
call :kill_one_port %1 quiet
shift
goto :kill_internal_loop

:kill_one_port
set "PORT=%~1"
set "MODE=%~2"
if "%MODE%"=="verbose" echo [SCAN] Port %PORT%...
netstat -ano | findstr :%PORT% | findstr LISTENING >nul 2>&1
if errorlevel 1 (
    if "%MODE%"=="verbose" echo [OK] Aucun processus sur le port %PORT%
    exit /b 0
)
set "FOUND=1"
if "%MODE%"=="verbose" (
    echo [INFO] Processus detectes sur le port %PORT% :
    netstat -ano | findstr :%PORT% | findstr LISTENING
    echo [KILL] Arret des processus port %PORT%...
) else (
    echo [CLEANUP] Port %PORT% occupe, arret en cours...
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :%PORT% ^| findstr LISTENING') do (
    taskkill /F /PID %%a /T >nul 2>&1
)
exit /b 0

:check_port
set "PORT=%~1"
netstat -ano | findstr :%PORT% | findstr LISTENING >nul 2>&1
if not errorlevel 1 set "REMAINING=1"
exit /b 0
