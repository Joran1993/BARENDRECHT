#!/bin/bash
# Start het bouwcontainer registratiesysteem

set -e
cd "$(dirname "$0")"

# Controleer of Python 3 beschikbaar is
if ! command -v python3 &> /dev/null; then
    echo "Python 3 is niet geïnstalleerd. Installeer via https://python.org"
    exit 1
fi

# Maak virtualenv aan als die nog niet bestaat
if [ ! -d "venv" ]; then
    echo "Virtualenv aanmaken..."
    python3 -m venv venv
fi

# Activeer virtualenv en installeer dependencies
source venv/bin/activate
pip install -q -r requirements.txt

# Vraag om API key als die nog niet is ingesteld
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo ""
    echo "Anthropic API key is niet ingesteld."
    echo "Haal een gratis key op via: https://console.anthropic.com"
    read -p "Vul je API key in (of druk Enter om door te gaan zonder AI): " key
    if [ -n "$key" ]; then
        export ANTHROPIC_API_KEY="$key"
    fi
fi

echo ""
echo "Systeem starten op http://localhost:8000"
echo "Open je browser en ga naar http://localhost:8000"
echo ""

python3 main.py
