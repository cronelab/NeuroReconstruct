@echo off
call conda activate neuro-recon
REM ============================================================
REM  NeuroReconstruct — Build standalone .exe
REM  Run this from the PROJECT ROOT (the folder containing
REM  both /frontend and /backend).
REM ============================================================

echo.
echo [1/4] Building React frontend...
cd frontend
call npm run build
if errorlevel 1 (
    echo ERROR: npm build failed. Make sure npm is installed and dependencies are up to date.
    pause
    exit /b 1
)
cd ..

echo.
echo [2/4] Installing PyInstaller into conda env...
call conda activate neuro-recon
pip install pyinstaller --quiet

echo.
echo [3/4] Copying build files into backend...
REM launcher.py and neuro_recon.spec should already be in /backend

echo.
echo [4/4] Running PyInstaller...
cd backend
pyinstaller neuro_recon.spec --clean --noconfirm
if errorlevel 1 (
    echo ERROR: PyInstaller failed. Check the output above for missing imports.
    pause
    exit /b 1
)
cd ..

echo.
echo ============================================================
echo  BUILD COMPLETE
echo  Your .exe is at:  backend\dist\NeuroReconstruct.exe
echo.
echo  Copy these to your colleague's machine (same folder):
echo    - NeuroReconstruct.exe
echo    - brain_viewer.db  (if you want existing data)
echo    - data\            (if you want existing reconstructions)
echo ============================================================
pause
