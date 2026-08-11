#!/bin/bash
# lawyer_fleet_public_url.sh — prints the CURRENT public tunnel URL for the lawyer fleet.
# The cloudflared quick-tunnel gets a new random URL every time it restarts (no domain
# configured), so this always re-reads the live log rather than caching a stale one.
URL=$(grep -o "https://[a-zA-Z0-9-]*\.trycloudflare\.com" /tmp/com.aryan.lawyer-fleet-tunnel.err.log 2>/dev/null | tail -1)
if [ -z "$URL" ]; then
    echo "No tunnel URL found yet — check: launchctl list | grep lawyer-fleet-tunnel"
    exit 1
fi
echo "$URL"
