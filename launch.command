#!/bin/bash
# Keertan Editz — 4K Bot Launcher

PYTHON="/opt/anaconda3/bin/python3"
APP_DIR="/Users/tedlasaikeertan/.gemini/antigravity/scratch/keertan_editz_app"

cd "$APP_DIR"

# If already running on port 5050, just open the browser
if lsof -Pi :5050 -sTCP:LISTEN -t >/dev/null ; then
    open "http://127.0.0.1:5050"
    exit 0
fi

# Start server in background
"$PYTHON" server.py &
SERVER_PID=$!

sleep 1.5
open "http://127.0.0.1:5050"

wait $SERVER_PID
