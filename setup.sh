#!/bin/bash

echo "🎬 MoviezWap Downloader Bot - Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check Python version
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "✅ Python version: $python_version"
echo ""

# Install dependencies
echo "📦 Installing required packages..."
pip install -q requests beautifulsoup4 lxml tqdm

echo ""
echo "✅ Setup complete!"
echo ""
echo "📝 To run the downloader bot:"
echo "   python3 moviezwap_downloader_bot.py"
echo ""
