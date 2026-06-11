@echo off
chcp 65001 >nul
title GitLab Quick MR - Build
echo ========================================
echo   GitLab Quick MR - One-click Build
echo ========================================
echo.

cd /d "%~dp0"

:: Step 1: Generate icon
echo [1/3] Generating icon...
python generate_icon.py
if errorlevel 1 (
    echo [ERROR] Icon generation failed!
    pause
    exit /b 1
)

:: Step 2: Clean old build
echo [2/3] Cleaning old build...
if exist dist\GitLab-QuickMR.exe del /f dist\GitLab-QuickMR.exe

:: Step 3: Build with PyInstaller
echo [3/3] Building exe (this may take a minute)...
pyinstaller ^
    --onefile ^
    --windowed ^
    --icon=app.ico ^
    --name=GitLab-QuickMR ^
    --clean ^
    --noconfirm ^
    --hidden-import=PyQt5.sip ^
    main.py

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed!
    pause
    exit /b 1
)

:: Copy config template
if not exist dist\config.example.xml copy config.example.xml dist\config.example.xml >nul

echo.
echo ========================================
echo   Build complete!
echo   Output: dist\GitLab-QuickMR.exe
echo ========================================
echo.
echo NOTE: Run GitLab-QuickMR.exe alongside
echo   a config.xml (or it will create one).
echo ========================================
pause
