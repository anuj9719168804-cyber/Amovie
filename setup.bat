@echo off
echo.
echo 🎬 MoviezWap Downloader Bot - Setup (Windows)
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo.

python --version >nul 2>&1
if %errorlevel% equ 0 (
    echo ✅ Python found
) else (
    echo ❌ Python not found! Install from python.org
    pause
    exit /b 1
)

echo.
echo 📦 Installing dependencies...
pip install -q requests beautifulsoup4 lxml tqdm

echo.
echo ✅ Setup complete!
echo.
echo 📝 To run the downloader:
echo    python moviezwap_downloader_bot.py
echo.
pause
