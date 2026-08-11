#!/usr/bin/env python3
"""
GitHub sync for 7-day test:
1. Fetch latest strategies from top 5 GitHub bots
2. Compare vs your edge
3. Push daily results to goodwearrinfo-aryan/edge-bots

Usage:
  python3 github_sync.py --fetch    (fetch latest bot code)
  python3 github_sync.py --push     (push daily results)
  python3 github_sync.py --compare  (analyze bot strategies)
"""

import subprocess
import json
from pathlib import Path
from datetime import datetime
import sys

GITHUB_REPO = "goodwearrinfo-aryan/edge-bots"
BOT_SOURCES = {
    "imdea": "https://github.com/FlexiWay/prediction-market-arbitrage",
    "polymaker": "https://github.com/warproxxx/poly-maker",
    "ent0n29": "https://github.com/ent0n29/polybot",
    "benjam1nCup": "https://github.com/Benjam1nCup/Polymarket-trading-bot-python-V2",
    "skharchikov": "https://github.com/skharchikov/polymarket-bot"
}

def git_cmd(cmd, cwd=None):
    """Run git command."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=30)
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def clone_bots_to_repo():
    """Fetch latest bot strategies into git repo."""
    print("\n[GIT] Fetching latest bot strategies...")

    # Ensure repo is cloned
    repo_path = Path("edge-bots-repo")
    if not repo_path.exists():
        print(f"  Cloning {GITHUB_REPO}...")
        success, out, err = git_cmd(["git", "clone", f"git@github.com:{GITHUB_REPO}.git", str(repo_path)])
        if not success:
            print(f"  ERROR: {err}")
            return False

    # Fetch submodules for each bot
    strategies_dir = repo_path / "strategies"
    strategies_dir.mkdir(exist_ok=True)

    for name, url in BOT_SOURCES.items():
        bot_path = strategies_dir / name

        if bot_path.exists():
            print(f"  {name}: pulling latest...")
            git_cmd(["git", "-C", str(bot_path), "pull"], cwd=repo_path)
        else:
            print(f"  {name}: cloning ({url})...")
            git_cmd(["git", "clone", "--depth", "1", url, str(bot_path)], cwd=repo_path)

    print("  ✓ Bot strategies synced")
    return True

def push_daily_results():
    """Push daily test results to GitHub."""
    print("\n[GIT] Pushing daily results...")

    repo_path = Path("edge-bots-repo")

    # Copy daily logs
    log_file = Path("SEVEN_DAY_TEST_LOG.json")
    if log_file.exists():
        import shutil
        shutil.copy(log_file, repo_path / "logs" / "daily_test_log.json")

    # Copy verdict
    verdict_file = Path("TEST_VERDICT.json")
    if verdict_file.exists():
        import shutil
        shutil.copy(verdict_file, repo_path / "verdicts" / f"verdict_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")

    # Git commit
    success, out, err = git_cmd(["git", "add", "."], cwd=repo_path)
    success, out, err = git_cmd(["git", "commit", "-m", f"[auto] daily test results — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}"], cwd=repo_path)

    if not success and "nothing to commit" not in err:
        print(f"  WARNING: {err}")

    # Push
    success, out, err = git_cmd(["git", "push", "origin", "main"], cwd=repo_path)
    if success:
        print(f"  ✓ Results pushed to {GITHUB_REPO}")
        return True
    else:
        print(f"  ERROR: {err}")
        return False

def compare_strategies():
    """Analyze strategies from GitHub bots."""
    print("\n[ANALYSIS] Comparing bot strategies...")

    repo_path = Path("edge-bots-repo")
    strategies_dir = repo_path / "strategies"

    comparison = {
        "timestamp": datetime.now().isoformat(),
        "bots_compared": len(BOT_SOURCES),
        "strategies": {}
    }

    for name, url in BOT_SOURCES.items():
        bot_path = strategies_dir / name

        if bot_path.exists():
            # Count code files
            py_files = len(list(bot_path.glob("**/*.py")))
            rs_files = len(list(bot_path.glob("**/*.rs")))

            # Check for backtest results
            backtest_files = list(bot_path.glob("**/backtest*.json"))

            comparison["strategies"][name] = {
                "url": url,
                "code_files": {"python": py_files, "rust": rs_files},
                "backtest_results": len(backtest_files),
                "last_commit": "TBD"
            }

    # Save comparison
    with open("GITHUB_BOT_COMPARISON.json", "w") as f:
        json.dump(comparison, f, indent=2)

    print(f"  ✓ Comparison saved to GITHUB_BOT_COMPARISON.json")
    return comparison

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    cmd = sys.argv[1]

    if cmd == "--fetch":
        return 0 if clone_bots_to_repo() else 1
    elif cmd == "--push":
        return 0 if push_daily_results() else 1
    elif cmd == "--compare":
        return 0 if compare_strategies() else 1
    else:
        print(f"Unknown command: {cmd}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
