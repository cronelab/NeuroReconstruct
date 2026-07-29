@echo off
setlocal
echo ============================================================
echo  NeuroReconstruct — Demo Build
echo ============================================================

:: 0. Activate the conda environment and verify the toolchain is reachable.
:: 'conda activate' only works in a shell where conda has been initialized, so
:: check for pyinstaller rather than trusting the activate call to have worked.
call conda activate neuro-recon >nul 2>&1
where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo ERROR: pyinstaller not found on PATH.
    echo   The 'neuro-recon' conda environment is not active.
    echo   Run this from an Anaconda Prompt, or run 'conda init cmd.exe' once
    echo   and open a new terminal, then re-run this script.
    pause
    exit /b 1
)

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

:: Copy existing data and database if present.
:: Two things are deliberately left out of the demo copy:
::   ct_cache\            regenerable CT threshold meshes (~1.4 GB). Rebuilt on
::                        first view of each threshold, roughly 19s, and only a
::                        couple of thresholds are ever used in a demo.
::   *_warp.nii.gz        MNI warp fields (~147 MB). Needed only to re-run an
::                        export, which regenerates them via ANTs.
:: Together these were 1.5 GB of a 1.9 GB package. The exclude file is written
:: with a relative name so a project path containing spaces cannot break
:: xcopy's /exclude parsing.
cd /d "%~dp0"
> demo_exclude.tmp echo \ct_cache\
>> demo_exclude.tmp echo \.envcache\
>> demo_exclude.tmp echo mri_to_mni_warp.nii.gz
>> demo_exclude.tmp echo mni_to_mri_invwarp.nii.gz
if exist "%~dp0backend\data" xcopy /e /i /q /exclude:demo_exclude.tmp "%~dp0backend\data" "%DIST%\data\"
del demo_exclude.tmp >nul 2>&1
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
