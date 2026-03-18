@echo off
setlocal
echo ============================================================
echo  NeuroReconstruct — Demo Build
echo ============================================================

:: 1. Build React frontend
echo.
echo [1/3] Building React frontend...
cd /d "%~dp0frontend"
call npm run build
if errorlevel 1 ( echo ERROR: npm build failed & pause & exit /b 1 )

:: 2. Run PyInstaller
echo.
echo [2/3] Bundling Python backend...
cd /d "%~dp0backend"
pyinstaller neuro_recon.spec --noconfirm
if errorlevel 1 ( echo ERROR: PyInstaller failed & pause & exit /b 1 )

:: 3. Assemble demo package
echo.
echo [3/3] Assembling demo package...
set DIST=%~dp0dist_demo
if exist "%DIST%" rmdir /s /q "%DIST%"
mkdir "%DIST%"

copy "%~dp0backend\dist\NeuroReconstruct.exe" "%DIST%\"

:: Copy existing data and database if present
if exist "%~dp0backend\data"          xcopy /e /i /q "%~dp0backend\data"          "%DIST%\data\"
if exist "%~dp0backend\brain_viewer.db" copy "%~dp0backend\brain_viewer.db" "%DIST%\"

:: Write a README
(
echo NeuroReconstruct Demo
echo =====================
echo 1. Double-click NeuroReconstruct.exe
echo 2. A browser window will open automatically at http://127.0.0.1:8000
echo 3. Login: admin / changeme
echo 4. Close the console window to stop the app.
echo.
echo The "data" folder and "brain_viewer.db" must stay next to the .exe.
) > "%DIST%\README.txt"

echo.
echo ============================================================
echo  Done!  Demo package is in:  %DIST%
echo ============================================================
pause
