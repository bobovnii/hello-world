#!/usr/bin/env bash
# One-command setup for Hamburg Deal Finder on your local machine.
# Clones repo (if needed), installs deps, starts MCP server + cloudflared tunnel.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/bobovnii/hello-world/claude/real-estate-deal-scraper-Vbj9C/scripts/phone_setup.sh | bash
# OR:
#   bash scripts/phone_setup.sh

set -e

REPO_URL="https://github.com/bobovnii/hello-world.git"
BRANCH="claude/real-estate-deal-scraper-Vbj9C"
REPO_DIR="$HOME/hello-world"

echo "=== Hamburg Deal Finder - Phone Setup ==="
echo ""

# 1. Clone repo if not already
if [ ! -d "$REPO_DIR" ]; then
    echo "→ Cloning repo to $REPO_DIR ..."
    git clone "$REPO_URL" "$REPO_DIR"
    cd "$REPO_DIR"
    git checkout "$BRANCH"
else
    echo "→ Repo already exists, pulling latest..."
    cd "$REPO_DIR"
    git fetch origin
    git checkout "$BRANCH"
    git pull origin "$BRANCH"
fi

# 2. Install Python deps
echo ""
echo "→ Installing Python dependencies..."
pip install -q -r requirements.txt
pip install -q "mcp[cli]" aiosqlite

# 3. Install cloudflared
echo ""
echo "→ Installing cloudflared..."
if ! command -v cloudflared &> /dev/null; then
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if command -v brew &> /dev/null; then
            brew install cloudflared
        else
            echo "   Homebrew not found. Installing cloudflared directly..."
            curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz | tar -xzC /tmp
            sudo mv /tmp/cloudflared /usr/local/bin/cloudflared
            sudo chmod +x /usr/local/bin/cloudflared
        fi
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        ARCH=$(uname -m)
        case "$ARCH" in
            x86_64) BIN="cloudflared-linux-amd64" ;;
            aarch64|arm64) BIN="cloudflared-linux-arm64" ;;
            *) echo "Unsupported arch: $ARCH"; exit 1 ;;
        esac
        sudo wget -q "https://github.com/cloudflare/cloudflared/releases/latest/download/$BIN" -O /usr/local/bin/cloudflared
        sudo chmod +x /usr/local/bin/cloudflared
    else
        echo "Unsupported OS: $OSTYPE"
        echo "Manual install: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
        exit 1
    fi
fi
echo "   cloudflared version: $(cloudflared --version | head -1)"

# 4. Verify Python setup
echo ""
echo "→ Verifying MCP server..."
python -c "
import sys; sys.path.insert(0, '.')
from mcp_server.server import mcp
tools = len(mcp._tool_manager._tools)
prompts = len(mcp._prompt_manager._prompts)
print(f'   OK: {tools} tools, {prompts} prompts registered')
"

# 5. Start MCP server
echo ""
echo "→ Starting MCP server on http://localhost:8000 ..."
python -m mcp_server.server --http > /tmp/mcp_server.log 2>&1 &
MCP_PID=$!
echo "   PID: $MCP_PID"
sleep 4

# Verify server is listening
if ! curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/mcp -H "Accept: application/json" | grep -qE "^(200|400|405)$"; then
    echo "   ERROR: MCP server didn't start. Check /tmp/mcp_server.log"
    tail /tmp/mcp_server.log
    kill $MCP_PID 2>/dev/null
    exit 1
fi
echo "   MCP server is up"

# 6. Start cloudflared tunnel
echo ""
echo "→ Starting Cloudflare tunnel (this can take 10-20s)..."
cloudflared tunnel --url http://localhost:8000 > /tmp/cloudflared.log 2>&1 &
TUNNEL_PID=$!
echo "   PID: $TUNNEL_PID"

# Wait for public URL
PUBLIC_URL=""
for i in {1..20}; do
    sleep 2
    PUBLIC_URL=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" /tmp/cloudflared.log | head -1 || true)
    if [ -n "$PUBLIC_URL" ]; then
        # Make sure tunnel is actually connected (not just URL printed)
        if grep -q "Registered tunnel connection" /tmp/cloudflared.log; then
            break
        fi
    fi
    echo "   Waiting for tunnel... (${i}/20)"
done

if [ -z "$PUBLIC_URL" ]; then
    echo "   ERROR: Tunnel didn't establish. Check /tmp/cloudflared.log"
    tail /tmp/cloudflared.log
    kill $MCP_PID $TUNNEL_PID 2>/dev/null
    exit 1
fi

echo ""
echo "=============================================================="
echo ""
echo "   SUCCESS! Your phone MCP URL:"
echo ""
echo "      $PUBLIC_URL/mcp"
echo ""
echo "=============================================================="
echo ""
echo "Next steps:"
echo "  1. On your phone, open the Claude app"
echo "  2. Settings → Connectors → Add custom connector"
echo "  3. Name: Hamburg Deals"
echo "  4. URL:  $PUBLIC_URL/mcp"
echo "  5. Transport: HTTP/SSE"
echo ""
echo "  Then just chat: 'Find 2-room apartments near DESY under 250k'"
echo ""
echo "Logs:"
echo "  MCP server:  tail -f /tmp/mcp_server.log"
echo "  Tunnel:      tail -f /tmp/cloudflared.log"
echo ""
echo "To stop everything:"
echo "  kill $MCP_PID $TUNNEL_PID"
echo ""
echo "Keep this terminal open. URL changes if you restart."
echo ""

# Keep running (wait for Ctrl+C)
trap "echo ''; echo 'Stopping...'; kill $MCP_PID $TUNNEL_PID 2>/dev/null; exit 0" INT TERM
wait
