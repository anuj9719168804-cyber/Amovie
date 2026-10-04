#!/bin/bash

# 🚀 MoviezWap Telegram Bot - Deployment Script

set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎬 MoviezWap Telegram Bot - Auto Deploy"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check if running as root
if [[ $EUID -eq 0 ]]; then
    echo "❌ Please don't run as root!"
    exit 1
fi

# Check Python version
echo "✅ Checking Python version..."
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Install it first:"
    echo "   sudo apt-get install python3 python3-pip"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | awk '{print $2}')
echo "   Python $PYTHON_VERSION found"

# Create directory
BOT_DIR="$HOME/moviezwap-telegram-bot"
if [ ! -d "$BOT_DIR" ]; then
    echo ""
    echo "📁 Creating bot directory: $BOT_DIR"
    mkdir -p "$BOT_DIR"
fi

cd "$BOT_DIR"

# Download files (or assume they exist)
if [ ! -f "moviezwap_telegram_bot.py" ]; then
    echo ""
    echo "📥 Downloading bot files..."
    # wget https://raw.githubusercontent.com/yourusername/repo/main/moviezwap_telegram_bot.py
    echo "❌ Please download bot files first"
    exit 1
fi

# Check .env file
if [ ! -f ".env" ]; then
    echo ""
    echo "⚠️  No .env file found!"
    echo "📋 Creating from example..."
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "✅ .env file created"
        echo "⚠️  Please edit .env with your credentials:"
        echo "   nano .env"
        exit 0
    else
        echo "❌ .env.example not found"
        exit 1
    fi
fi

# Install dependencies
echo ""
echo "📦 Installing dependencies..."
pip install -r requirements_telegram_bot.txt --quiet

# Create directories
mkdir -p downloads logs

# Option: Use systemd service
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Setup Options:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "[1] Run directly (test)"
echo "[2] Run as background service"
echo "[3] Setup systemd service (auto-start)"
echo "[0] Exit"
echo ""

read -p "Choose option: " choice

case $choice in
    1)
        echo ""
        echo "🚀 Starting bot..."
        python3 moviezwap_telegram_bot.py
        ;;
    2)
        echo ""
        echo "🚀 Starting bot in background..."
        nohup python3 moviezwap_telegram_bot.py > bot.log 2>&1 &
        sleep 2
        PID=$!
        echo "✅ Bot started with PID: $PID"
        echo "📝 Logs: tail -f $BOT_DIR/bot.log"
        echo "🛑 Stop: kill $PID"
        ;;
    3)
        echo ""
        echo "🔧 Setting up systemd service..."
        
        if [ ! -f "moviezwap-bot.service" ]; then
            echo "❌ moviezwap-bot.service not found"
            exit 1
        fi
        
        # Update paths in service file
        sed -i "s|/home/ubuntu|$HOME|g" moviezwap-bot.service
        
        # Copy service file
        sudo cp moviezwap-bot.service /etc/systemd/system/
        
        # Enable service
        sudo systemctl daemon-reload
        sudo systemctl enable moviezwap-bot.service
        sudo systemctl start moviezwap-bot.service
        
        echo "✅ Service installed!"
        echo ""
        echo "📋 Useful commands:"
        echo "   sudo systemctl start moviezwap-bot    # Start"
        echo "   sudo systemctl stop moviezwap-bot     # Stop"
        echo "   sudo systemctl restart moviezwap-bot  # Restart"
        echo "   sudo systemctl status moviezwap-bot   # Status"
        echo "   sudo journalctl -u moviezwap-bot -f   # Logs"
        ;;
    0)
        echo "Goodbye! 👋"
        exit 0
        ;;
    *)
        echo "❌ Invalid option"
        exit 1
        ;;
esac

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Done!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
