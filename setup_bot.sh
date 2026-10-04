#!/bin/bash

# MoviezWap Telegram Bot - Setup Script

echo ""
echo "╔═══════════════════════════════════════════════════════╗"
echo "║  🎬 MoviezWap Telegram Bot - Setup                   ║"
echo "╚═══════════════════════════════════════════════════════╝"
echo ""

# Check Python
echo "✅ Checking Python..."
python3 --version

# Check if .env exists
if [ ! -f ".env" ]; then
    echo ""
    echo "⚠️  No .env file found!"
    if [ -f ".env.example" ]; then
        echo "📋 Creating .env from .env.example..."
        cp .env.example .env
        echo "✅ .env file created"
        echo ""
        echo "⚠️  IMPORTANT: Edit .env with your credentials:"
        echo ""
        echo "   nano .env"
        echo ""
        echo "You need:"
        echo "  • API_ID (from https://my.telegram.org)"
        echo "  • API_HASH (from https://my.telegram.org)"
        echo "  • BOT_TOKEN (from @BotFather)"
        echo "  • OWNER_ID (your Telegram user ID)"
        echo ""
        exit 0
    fi
fi

# Install dependencies
echo ""
echo "📦 Installing Python dependencies..."
pip install -r requirements_telegram_bot.txt

# Create directories
echo ""
echo "📁 Creating directories..."
mkdir -p downloads logs

# Check .env
echo ""
echo "🔍 Verifying .env file..."
if grep -q "^BOT_TOKEN=" .env && [ "$(grep '^BOT_TOKEN=' .env | cut -d= -f2)" != "" ]; then
    echo "✅ BOT_TOKEN set"
else
    echo "❌ BOT_TOKEN not set!"
    exit 1
fi

if grep -q "^API_ID=" .env && [ "$(grep '^API_ID=' .env | cut -d= -f2)" != "0" ]; then
    echo "✅ API_ID set"
else
    echo "❌ API_ID not set!"
    exit 1
fi

if grep -q "^API_HASH=" .env && [ "$(grep '^API_HASH=' .env | cut -d= -f2)" != "" ]; then
    echo "✅ API_HASH set"
else
    echo "❌ API_HASH not set!"
    exit 1
fi

echo ""
echo "╔═══════════════════════════════════════════════════════╗"
echo "║  ✅ Setup Complete!                                  ║"
echo "╚═══════════════════════════════════════════════════════╝"
echo ""
echo "🚀 To start the bot, run:"
echo ""
echo "   python3 moviezwap_telegram_bot.py"
echo ""
echo "📖 For detailed guide, see: TELEGRAM_BOT_GUIDE.md"
echo ""
