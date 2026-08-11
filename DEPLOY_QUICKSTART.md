# Deploy Quickstart — paste-once per machine

Companion to `CLOUD_SETUP.md`. Same steps, but every command reads one variable so
you set your server IP **once per terminal session** instead of editing each line.

Provider/region decided: **Hetzner CX22, Nuremberg or Falkenstein (DE), Ubuntu 24.04.**
(Fallback if Hetzner signup blocks you: DigitalOcean Frankfurt FRA1, $6.)
Paper only — no API keys, no real orders anywhere in this stack.

---

## 0) Create the box
Hetzner Cloud → new project → Add Server:
- Location: **Nuremberg** or **Falkenstein** (Germany)
- Image: **Ubuntu 24.04**
- Type: **CX22** (2 vCPU / 4 GB — cheapest shared line; CX line is EU-only)
- Add your SSH key (or use the emailed root password)

Copy the server's public IPv4 when it's ready.

---

## 1) ON THE SERVER — verify access FIRST (fail fast)
SSH in, then paste this whole block. If you don't see `gamma: 200`, stop.

```bash
# paste your server IP once:
export SRV=root@PASTE_SERVER_IP

ssh -o StrictHostKeyChecking=accept-new $SRV
```

Now you're on the server. Test reachability before anything else:

```bash
curl -sS -m 10 -o /dev/null -w "gamma: %{http_code}\n" \
  "https://gamma-api.polymarket.com/markets?limit=1&closed=false"
```

- `gamma: 200` → region is clean. Continue.
- `gamma: 000` / timeout → this region is blocked too. Exit, **destroy the server**,
  recreate in **Finland (Helsinki)** or **Netherlands**, repeat Step 1. Do NOT proceed.

Then install deps (still on the server):

```bash
apt update && apt install -y python3 python3-pip rsync tmux
python3 --version
exit   # back to your Mac for the upload
```

---

## 2) ON YOUR MAC — upload the project
Open a normal Terminal on your Mac and paste:

```bash
# paste the SAME server IP once:
export SRV=root@PASTE_SERVER_IP

rsync -av --exclude '__pycache__' --exclude '*.pyc' \
  --exclude '*.log' --exclude 'analytics.db' --exclude 'moves_log.jsonl' \
  ~/Documents/polymarket/ $SRV:/root/polymarket/
```

(The excludes skip multi-hundred-MB logs/DB you don't need on the box. Re-run this
same command anytime to push code changes.)

---

## 3) ON THE SERVER — run it under systemd (auto-restart + survives reboot)

```bash
# paste the SAME server IP once:
export SRV=root@PASTE_SERVER_IP
ssh $SRV
```

Now on the server:

```bash
cd /root/polymarket
chmod +x watchdog_loop.sh
cp polymarket-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now polymarket-bot
systemctl status polymarket-bot      # want: "active (running)"
```

---

## 4) ON THE SERVER — confirm it's actually trading
Wait ~2 minutes, then:

```bash
tail -8 /root/polymarket/scalp_lab.log     # want: "fetched N markets" with N > 0
python3 /root/polymarket/leg_health.py
```

`N > 0` = unblocked. The legs (incl. the makerH maker book) start filling on their own.
If `scalp_lab.log` still shows `0 markets` even with `gamma: 200`, that's a code/path
issue, not network — tell me and I'll look.

---

## 5) (optional) pull state back to your Mac for the local reports
Run on your **Mac** whenever you want the dashboard / 3x-day emails to reflect live fills:

```bash
export SRV=root@PASTE_SERVER_IP
rsync -av \
  $SRV:/root/polymarket/scalp_lab_state.json \
  $SRV:/root/polymarket/scalp_engine_state.json \
  $SRV:/root/polymarket/scalp_lab.log \
  ~/Documents/polymarket/
```

---

## After any code change
On Mac: re-run Step 2 rsync. On server: `systemctl restart polymarket-bot`.

## Watching it
```bash
ssh $SRV 'systemctl status polymarket-bot'          # alive?
ssh $SRV 'journalctl -u polymarket-bot -f'          # live log (Ctrl-C to stop)
ssh $SRV 'tail -5 /root/polymarket/scalp_lab.log'   # fetching markets?
```

## Boundaries
- I can't create the server, pay, or SSH in — those are yours. Every command here is
  written out; you paste it.
- Paper only until maker Phase A proves out. No keys, no real orders.
- The Mac bot (PID on record) keeps running stuck in its 0-market loop. Once the VPS is
  fetching, kill the Mac one to avoid two confusing state files: `./stop_bot.command`.
