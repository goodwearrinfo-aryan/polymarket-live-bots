#!/bin/bash
# cloud_bootstrap.sh — ONE command to make the Polymarket paper bot fully autonomous
# on a fresh Ubuntu VPS (non-US, non-India region). Paper only: NO keys, NO real money.
#
# Usage (on the server, after `git clone` or rsync of this project):
#   cd /root/polymarket && sudo bash cloud_bootstrap.sh
#
# What it does: verifies the region reaches Polymarket, installs deps, installs the
# systemd service (auto-restart on crash + auto-start on reboot), and starts it.
# After this the box runs itself — no Mac, no tmux, no babysitting.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "[1/6] Verifying THIS region can reach Polymarket (fail fast)…"
code=$(curl -sS -m 12 -o /dev/null -w "%{http_code}" \
  "https://gamma-api.polymarket.com/markets?limit=1&closed=false" || echo 000)
if [ "$code" != "200" ]; then
  echo "  gamma: $code  → THIS REGION IS BLOCKED (US/India regions do this)."
  echo "  Destroy this server, recreate in Germany/Netherlands/Singapore, re-run."
  exit 1
fi
echo "  gamma: 200 ✓  clean region"

say "[2/6] Installing system packages…"
apt-get update -qq
apt-get install -y -qq python3 python3-pip perl curl >/dev/null
echo "  python3 $(python3 --version 2>&1 | awk '{print $2}') ✓"

say "[3/6] Installing Python deps…"
pip3 install -q -r "$DIR/requirements.txt" 2>/dev/null \
  || pip3 install -q --break-system-packages -r "$DIR/requirements.txt"
echo "  deps installed ✓"

say "[4/6] SMTP creds for autonomous email reports (optional)…"
if [ ! -f "$DIR/.smtp_creds.json" ]; then
  cp "$DIR/.smtp_creds.json.example" "$DIR/.smtp_creds.json" 2>/dev/null || true
fi
chmod 600 "$DIR/.smtp_creds.json" 2>/dev/null || true
echo "  -> to get daily email reports, put a Gmail App Password in $DIR/.smtp_creds.json"
echo "     (the bot runs fine without it; reports just won't email)."

say "[5/6] Installing systemd service (auto-restart + survives reboot)…"
# Rewrite the service's paths to wherever this project actually lives.
sed "s#/root/polymarket#$DIR#g" "$DIR/polymarket-bot.service" \
  > /etc/systemd/system/polymarket-bot.service
systemctl daemon-reload
systemctl enable --now polymarket-bot
echo "  service enabled + started ✓"

say "[6/6] Status:"
sleep 4
systemctl --no-pager --lines=0 status polymarket-bot | head -6 || true
echo
say "AUTONOMOUS. systemd now restarts it on crash AND on every reboot — no Mac needed."
echo "  Watch live:      journalctl -u polymarket-bot -f"
echo "  Is it trading?   tail -5 $DIR/scalp_lab.log     (want 'fetched N markets', N>0)"
echo "  Fade gate:       python3 $DIR/fade_checkpoint.py"
echo "  After code pull:  systemctl restart polymarket-bot"
