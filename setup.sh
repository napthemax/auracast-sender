#!/bin/bash
# Auracast Sender – Installation för macOS
# =========================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

app_version() {
    local ver sha
    ver=$(tr -d '[:space:]' < "$SCRIPT_DIR/VERSION" 2>/dev/null || echo "unknown")
    sha=$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || true)
    if [ -n "$sha" ]; then
        echo "$ver ($sha)"
    else
        echo "$ver"
    fi
}

echo ""
echo "======================================"
echo "  Auracast Sender – Installation"
echo "  $(app_version)"
echo "======================================"
echo ""

# Homebrew
if ! command -v brew &>/dev/null; then
    echo "[1/4] Installerar Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
    echo "[1/4] Homebrew OK"
fi

# Python
echo "[2/4] Kontrollerar Python..."
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
    if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
        echo "      Python $PY_VER är för gammalt – installerar 3.12..."
        brew install python@3.12
        PYTHON="$(brew --prefix)/bin/python3.12"
    else
        echo "      Python $PY_VER – OK"
        PYTHON="python3"
    fi
else
    brew install python@3.12
    PYTHON="$(brew --prefix)/bin/python3.12"
fi

# Virtuell miljö
echo "[3/4] Skapar virtuell miljö..."
if [ ! -d "$VENV" ]; then
    "$PYTHON" -m venv "$VENV"
fi
source "$VENV/bin/activate"

# Paket – ALLA beroenden från requirements.txt (även FastAPI/uvicorn för servern)
echo "[4/4] Installerar Python-paket..."
pip install --upgrade pip -q
pip install -r "$SCRIPT_DIR/requirements.txt" -q
echo "      Alla paket från requirements.txt installerade."

# Startscript: GUI
cat > "$SCRIPT_DIR/start_auracast.command" << 'LAUNCHER'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.venv/bin/activate"
cd "$SCRIPT_DIR"
python3 main.py
LAUNCHER
chmod +x "$SCRIPT_DIR/start_auracast.command"

# Startscript: API-server (för Lovable-webappen)
cat > "$SCRIPT_DIR/start_server.command" << 'LAUNCHER'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.venv/bin/activate"
cd "$SCRIPT_DIR"
python3 server.py
LAUNCHER
chmod +x "$SCRIPT_DIR/start_server.command"

echo ""
echo "======================================"
echo "  Klar!  $(app_version)"
echo "======================================"
echo ""
echo "  Starta API-servern (för webapp): dubbelklicka start_server.command"
echo "  Starta lokalt GUI:               dubbelklicka start_auracast.command"
echo ""
echo "  VIKTIGT – Hårdvarukrav:"
echo "  Macens inbyggda Bluetooth kan INTE sända Auracast."
echo "  En USB-dongle krävs och detekteras automatiskt:"
echo ""
echo "  • Flairmesh FMA120    – för HÖRAPPARATER (standard broadcast)"
echo "  • Sennheiser BTD 700  – för hörlurar/högtalare (high quality)"
echo ""
echo "  OBS: Hörapparater kan bara ta emot standard/public-"
echo "  broadcast. BTD 700 sänder endast high quality-läge."
echo ""
echo "  Systemljud-routing (valfritt):"
echo "    brew install blackhole-2ch"
echo "    Välj 'BlackHole 2ch' som utdata i Systeminst. > Ljud"
echo ""
