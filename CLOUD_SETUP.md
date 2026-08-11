# Cloud Setup — run the Polymarket paper bot on a VPS with clean access

Why: your home network and mobile carrier both block Polymarket (regional block).
A small Linux server in a clean region reaches it fine and runs 24/7 (your laptop
doesn't have to be on). This is paper-only — NO API keys, NO real money involved.

Cost: ~$4–6/month. Time: ~20 minutes.

---

## ⚡ FASTEST PATH — one command (autonomous)

After you have a VPS (Step 0) and the code on it (git clone or rsync, Step 3):

```
cd /root/polymarket && sudo bash cloud_bootstrap.sh
```

That single script verifies the region reaches Polymarket (fails fast if blocked),
installs deps, installs the **systemd** service (auto-restart on crash + auto-start on
reboot), and starts it. After it prints "AUTONOMOUS", the box runs itself — no Mac, no
tmux, no babysitting. Put a Gmail App Password in `.smtp_creds.json` to also get daily
email reports. Steps 2–5 below are the same thing done by hand if you prefer.

### Getting the code there — git (recommended, "stored in cloud")
On your **Mac**, push the repo once to a private GitHub/GitLab repo (create the empty repo first):
```
cd ~/Documents/polymarket
git remote add origin git@github.com:YOUR_USER/polymarket.git
git push -u origin main
```
Then on the **server**: `git clone git@github.com:YOUR_USER/polymarket.git /root/polymarket`.
After future changes: `git push` on the Mac, then `git pull && systemctl restart polymarket-bot`
on the server. Secrets/state/logs are .gitignored, so nothing sensitive reaches the cloud.

---

## Step 0 — Pick a provider + REGION (this matters most)

Region must be **NOT United States** (Polymarket geo-blocks US) and **NOT India**
(where you're blocked). Good choices: **Germany, Netherlands, UK, Singapore**.

Cheapest good options:
- **Hetzner** (hetzner.com/cloud) — ~€4/mo, regions: Falkenstein/Nuremberg (DE), Helsinki (FI). Best value.
- **DigitalOcean** — ~$6/mo, pick Frankfurt (FRA1), Amsterdam (AMS3), London (LON1), or Singapore (SGP1).
- **Vultr / Linode** — similar, pick a EU/SG region.

Create the smallest instance: **Ubuntu 24.04 (or 22.04), 1 vCPU / 1 GB RAM, ~25GB.**
Add your SSH key during creation (or use the password they email you).

---

## Step 1 — VERIFY ACCESS BEFORE ANYTHING ELSE (fail fast)

SSH in, then test that this server can actually reach Polymarket:
```
ssh root@YOUR_SERVER_IP
curl -sS -m 10 -o /dev/null -w "gamma: %{http_code}\n" "https://gamma-api.polymarket.com/markets?limit=1&closed=false"
```
- `gamma: 200` → this region is clean. Continue.
- `gamma: 000` / timeout → that region is ALSO blocked (rare, but US regions will do this). Destroy it, recreate in a different region, retest. Don't proceed until you see 200.

---

## Step 2 — Install Python (Ubuntu already has python3)
```
apt update && apt install -y python3 python3-pip rsync tmux
python3 --version
```
(The live legs use only the standard library. `pip`/sklearn are only needed if you
also want to run ML training on the box — skip unless you do.)

---

## Step 3 — Upload the bot from your Mac

Run this **on your Mac** (not the server), from a normal Terminal:
```
rsync -av --exclude '__pycache__' --exclude '*.pyc' \
  ~/Documents/polymarket/ root@YOUR_SERVER_IP:/root/polymarket/
```
This copies your whole project (code + current state) up. Re-run it anytime to push code changes.

---

## Step 4 — Start the bot in a tmux session (survives logout)

Back on the **server**:
```
cd /root/polymarket
chmod +x watchdog_loop.sh
tmux new -s bot
./watchdog_loop.sh
```
Then press `Ctrl-b` then `d` to detach (the bot keeps running). Re-attach later with `tmux attach -t bot`.

---

## Step 5 — Confirm it's actually trading

After ~2 minutes, on the server:
```
tail -5 /root/polymarket/scalp_lab.log
python3 /root/polymarket/leg_health.py
```
You want `scalp_lab.log` to say **"fetched N markets"** with N > 0 (not 0). Once it
does, the legs — including the new makerH maker book — start filling on their own.

---

## Step 6 (optional) — get the data back to your Mac for the reports

Your Cowork reports/dashboard read the state files locally. To keep them working,
pull the server's state down to your Mac periodically. On your **Mac**:
```
rsync -av root@YOUR_SERVER_IP:/root/polymarket/scalp_lab_state.json \
         root@YOUR_SERVER_IP:/root/polymarket/scalp_engine_state.json \
         root@YOUR_SERVER_IP:/root/polymarket/scalp_lab.log \
         ~/Documents/polymarket/
```
Then `python3 leg_health.py` / `honest_report.py` on your Mac shows the live numbers,
and the 3x/day status emails reflect real fills. (Or just run those on the server.)

---

## Step 4b — systemd instead of tmux (RECOMMENDED: auto-restart + survives reboot)

tmux works but dies if the box reboots. systemd keeps the bot alive permanently:
restarts it within 10s if it ever crashes, and starts it automatically on every reboot.
The `polymarket-bot.service` file is already in your project folder (uploaded by the rsync in Step 3).

On the **server**:
```
# install the service (the file came up with your project)
cp /root/polymarket/polymarket-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now polymarket-bot        # start now + on every boot
systemctl status polymarket-bot              # should say "active (running)"
```
If you used tmux in Step 4, stop it first: `tmux kill-session -t bot`.

Watching it ("looking after it"):
```
systemctl status polymarket-bot              # is it alive?
journalctl -u polymarket-bot -f              # live log stream (Ctrl-C to stop)
tail -5 /root/polymarket/scalp_lab.log       # is it fetching markets?
```
After a code change: re-run the Step 3 rsync from your Mac, then on the server
`systemctl restart polymarket-bot`.

This is the "look after it" layer: **systemd auto-restarts on crash and on reboot**,
and your Cowork scheduled tasks (3x/day status, daily leg-health, weekly bootstrap)
report on it — as long as you sync state back per Step 6. I can't reach your VPS
directly, but between systemd and those reports it watches itself.

---

## Notes / boundaries
- Paper only. There are NO keys and NO real orders anywhere in this stack — keep it that way until maker Phase A proves out.
- I can't create the server, enter payment, or SSH in for you — those are yours. I wrote every command; you run them.
- If `gamma: 200` fails in EVERY region you try (very unlikely), tell me and we'll rethink.
- Pick a region close-ish to you for lower latency if you later pursue the latency edge — Singapore is a good middle ground from India.
