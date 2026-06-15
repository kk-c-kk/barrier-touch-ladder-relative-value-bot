# Deploying the logger on a VPS

Goal: run `scripts/run_logger.py` 24/7 so it accumulates touch-ladder snapshots and
resolved outcomes. Assumes Ubuntu/Debian and a user `ubuntu`; adjust paths if yours
differ. Read-only public APIs only — no exchange keys needed for logging.

## 1. SSH into the VPS

```bash
ssh ubuntu@YOUR_VPS_IP
```

## 2. Generate an SSH key on the VPS and add it to GitHub

This lets the VPS `git pull` over SSH. A **deploy key** (repo-scoped, read-only) is
the safest choice.

```bash
# on the VPS
ssh-keygen -t ed25519 -C "vps-ladder-logger" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub      # copy this whole line
```

Then on GitHub: **repo → Settings → Deploy keys → Add deploy key**, paste the public
key, leave "Allow write access" unchecked (the VPS only needs to pull). Verify:

```bash
ssh -T git@github.com          # expect: "Hi kk-c-kk/... You've successfully authenticated"
```

> Prefer a deploy key over adding the key to your personal account — it scopes VPS
> access to just this repo, so a compromised VPS can't touch your other repos.

## 3. Clone over SSH

```bash
cd ~
git clone git@github.com:kk-c-kk/barrier-touch-ladder-relative-value-bot.git
cd barrier-touch-ladder-relative-value-bot
```

## 4. Python env + deps

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # sanity: 23 passing
```

## 5. Config + data dir

```bash
cp config.example.yaml config.yaml     # edit if needed; defaults are fine to start
mkdir -p data                          # /data is gitignored; create it on the box
```

Smoke-test discovery against the live API before committing to a long run:

```bash
.venv/bin/python scripts/probe.py      # expect rungs>0 for BTC/ETH/SOL/XRP
```

## 6. Run it 24/7 with systemd (recommended)

```bash
sudo cp deploy/ladder-logger.service /etc/systemd/system/ladder-logger.service
# if your user or path isn't ubuntu / ~/barrier-touch-ladder-relative-value-bot,
# edit User= and WorkingDirectory=/ExecStart= in that file first.
sudo systemctl daemon-reload
sudo systemctl enable --now ladder-logger
```

Watch it:

```bash
systemctl status ladder-logger
journalctl -u ladder-logger -f         # live logs; expect "discovered N live rungs"
```

### Quick alternative (no root): tmux

```bash
sudo apt install -y tmux
tmux new -s logger
.venv/bin/python scripts/run_logger.py
# detach: Ctrl-b then d   |   reattach: tmux attach -t logger
```

## 7. Pulling updates later

```bash
cd ~/barrier-touch-ladder-relative-value-bot
git pull
sudo systemctl restart ladder-logger   # (or restart your tmux process)
```

## 8. Verify data is actually accumulating (don't trust "it's running")

```bash
sqlite3 data/ladders.sqlite \
  "SELECT (SELECT COUNT(*) FROM snapshots) AS snaps,
          (SELECT COUNT(DISTINCT rung_slug) FROM rungs) AS rungs,
          (SELECT COUNT(*) FROM resolutions) AS resolved,
          (SELECT datetime(MAX(ts),'unixepoch') FROM snapshots) AS last_snap;"
```

`snaps` and `rungs` should climb every minute. `resolved` stays 0 until the first
**daily** ladder settles (~1 day) — those are your fastest validation data; weekly
and monthly fill in later. Once `resolved` is meaningful, run the validation report:

```bash
.venv/bin/python scripts/validate.py
```

If it's running but `snaps` isn't growing, read `journalctl -u ladder-logger` — a
silent feed failure looks like "running" but logs the real error there.
