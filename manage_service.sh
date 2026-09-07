#!/bin/bash
# Keertan Editz Studio - macOS Daemon Control Script

PLIST="$HOME/Library/LaunchAgents/com.keertan.editz.studio.plist"

case "$1" in
    start)
        echo "Starting Keertan Editz Studio 24/7 background service..."
        launchctl load -w "$PLIST" 2>/dev/null || true
        echo "✓ Started! Running at http://127.0.0.1:5050"
        ;;
    stop)
        echo "Stopping Keertan Editz Studio service..."
        launchctl unload "$PLIST" 2>/dev/null || true
        lsof -ti :5050 | xargs kill -9 2>/dev/null || true
        echo "✓ Stopped."
        ;;
    restart)
        echo "Restarting Keertan Editz Studio service..."
        launchctl unload "$PLIST" 2>/dev/null || true
        lsof -ti :5050 | xargs kill -9 2>/dev/null || true
        sleep 1
        launchctl load -w "$PLIST"
        echo "✓ Restarted! Running at http://127.0.0.1:5050"
        ;;
    status)
        echo "=== Keertan Editz Studio Service Status ==="
        launchctl list | grep "keertan" || echo "Service not running in launchd"
        curl -s http://127.0.0.1:5050/api/dm/status | grep "enabled" && echo "✓ Web server & DM listener active on port 5050" || echo "✗ Web server not responding on port 5050"
        ;;
    logs)
        tail -f "$HOME/.gemini/antigravity/scratch/keertan_editz_app/studio_daemon.log"
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
