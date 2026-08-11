#!/usr/bin/env python3
"""
Automatic Obsidian Vault Sync to GitHub
Runs hourly: detect changes, commit, push
Fail-safe: no error if clean or offline
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

VAULT_DIR = Path.home() / "Documents" / "PolymarketVault"
LOG_FILE = Path("/tmp/vault-sync.log")

def log_msg(msg):
    """Append to log."""
    ts = datetime.utcnow().isoformat()
    line = f"[{ts}] {msg}\n"
    with open(LOG_FILE, "a") as f:
        f.write(line)
    print(msg, file=sys.stderr)

def run_cmd(cmd, cwd=VAULT_DIR):
    """Run shell command, return (exit_code, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=60
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout"
    except Exception as e:
        return -1, "", str(e)

def main():
    log_msg("=== Vault Sync Start ===")

    # Check if vault exists
    if not VAULT_DIR.exists():
        log_msg(f"Vault not found at {VAULT_DIR}")
        return 1

    # Check if git repo
    git_dir = VAULT_DIR / ".git"
    if not git_dir.exists():
        log_msg("Vault is not a git repo")
        return 1

    # Check for changes
    code, out, err = run_cmd("git status --porcelain")
    if code != 0:
        log_msg(f"git status failed: {err}")
        return 1

    if not out.strip():
        log_msg("No changes — skipping")
        return 0

    changes = len(out.strip().split("\n"))
    log_msg(f"Found {changes} changed files")

    # Stage all changes
    code, _, err = run_cmd("git add -A")
    if code != 0:
        log_msg(f"git add failed: {err}")
        return 1

    # Commit
    msg = f"Auto-sync vault: {changes} files changed"
    code, _, err = run_cmd(
        f'git commit -m "{msg}\n\nCo-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"'
    )
    if code != 0:
        # Maybe nothing to commit
        if "nothing to commit" in err or "nothing to commit" in out:
            log_msg("No changes to commit")
            return 0
        log_msg(f"git commit failed: {err}")
        return 1

    log_msg("Committed changes")

    # Pull before push (fail-safe)
    code, _, err = run_cmd("git pull --rebase origin main 2>&1")
    if code != 0:
        log_msg(f"git pull failed (may be offline): {err[:100]}")
        return 1

    log_msg("Pulled latest from GitHub")

    # Push
    code, out, err = run_cmd("git push origin main 2>&1")
    if code != 0:
        log_msg(f"git push failed: {err[:100]}")
        return 1

    log_msg("Pushed to GitHub ✓")
    log_msg("=== Vault Sync Complete ===")
    return 0

if __name__ == "__main__":
    sys.exit(main())
