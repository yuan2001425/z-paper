#!/usr/bin/env bash
set -e

echo "========================================"
echo "   Z-Paper Local Paper Manager"
echo "========================================"
echo

# ── Python 检查 ───────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 not found. Please install Python 3.11+"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(sys.version_info.minor)")
if [ "$PYTHON_VERSION" -lt 11 ]; then
    echo "[ERROR] Python 3.11+ required."
    exit 1
fi

# ── 后端环境 ──────────────────────────────────────────────────────────────────
echo "[1/3] Checking backend environment..."
cd backend

if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
echo "Installing/updating backend dependencies..."
pip install -r requirements.txt -q

cd ..

# ── 前端构建（首次）──────────────────────────────────────────────────────────
if [ ! -f "frontend/dist/index.html" ]; then
    echo
    echo "[2/3] Building frontend (first run only, requires Node.js)..."
    if ! command -v node &>/dev/null; then
        echo "[ERROR] Node.js not found. Please install Node.js 18+ for first-time setup."
        exit 1
    fi
    cd frontend
    [ ! -d "node_modules" ] && npm install
    npm run build
    cd ..
else
    echo "[2/3] Frontend ready."
fi

# ── 启动后端 ──────────────────────────────────────────────────────────────────
echo
echo "[3/3] Starting Z-Paper..."
echo
echo "========================================"
echo "   http://localhost:8000"
echo "   Press Ctrl+C to stop."
echo "========================================"
echo

# 延迟 2 秒后在后台打开浏览器
(sleep 2 && (xdg-open http://localhost:8000 2>/dev/null || open http://localhost:8000 2>/dev/null)) &

cd backend
source venv/bin/activate
uvicorn app.main:app --port 8000
