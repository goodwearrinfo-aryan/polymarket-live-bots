#!/usr/bin/env python3
"""
Test GitHub Polymarket bots locally.
Clones + analyzes top 5 bots for strategy validation.

Usage:
  python3 test_github_bots.py

Output:
  - GITHUB_BOTS_TEST_RESULTS.json
  - Cloned repos in ./github_bots_temp/
"""

import json
import subprocess
from pathlib import Path
from datetime import datetime

BOTS_TO_TEST = [
    {
        "name": "IMDEA Arbitrage",
        "url": "https://github.com/FlexiWay/prediction-market-arbitrage",
        "type": "arbitrage",
        "test_type": "code_analysis"
    },
    {
        "name": "warproxxx/poly-maker",
        "url": "https://github.com/warproxxx/poly-maker",
        "type": "market_maker",
        "test_type": "code_analysis"
    },
    {
        "name": "ent0n29/polybot",
        "url": "https://github.com/ent0n29/polybot",
        "type": "multi_strategy",
        "test_type": "code_analysis"
    },
    {
        "name": "Benjam1nCup/Polymarket-trading-bot-python-V2",
        "url": "https://github.com/Benjam1nCup/Polymarket-trading-bot-python-V2",
        "type": "copy_trading",
        "test_type": "code_analysis"
    },
    {
        "name": "skharchikov/polymarket-bot",
        "url": "https://github.com/skharchikov/polymarket-bot",
        "type": "ml_ensemble",
        "test_type": "code_analysis"
    }
]

TEMP_DIR = Path("./github_bots_temp")
RESULTS_FILE = Path("GITHUB_BOTS_TEST_RESULTS.json")

def clone_repo(url, name):
    """Clone a repo and return path."""
    repo_path = TEMP_DIR / name.replace("/", "_")

    if repo_path.exists():
        print(f"  {name}: already cloned, skipping")
        return repo_path

    print(f"  Cloning {name}...")
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(repo_path)],
            capture_output=True,
            timeout=30
        )
        return repo_path
    except Exception as e:
        print(f"  ERROR cloning {name}: {e}")
        return None

def analyze_repo(repo_path, bot_name, bot_type):
    """Analyze repo for strategy, edge claims, performance."""

    analysis = {
        "name": bot_name,
        "type": bot_type,
        "path": str(repo_path),
        "files_found": [],
        "strategy_description": "N/A",
        "edge_claims": [],
        "performance_metrics": [],
        "tech_stack": [],
        "status": "not_found"
    }

    if not repo_path.exists():
        return analysis

    analysis["status"] = "cloned"

    # List Python/JS files
    try:
        result = subprocess.run(
            ["find", str(repo_path), "-type", "f", "-name", "*.py", "-o", "-name", "*.ts", "-o", "-name", "*.js"],
            capture_output=True,
            text=True,
            timeout=10
        )
        analysis["files_found"] = result.stdout.strip().split("\n")[:10]  # First 10
    except:
        pass

    # Check for README
    readme = repo_path / "README.md"
    if readme.exists():
        try:
            with open(readme) as f:
                content = f.read()[:2000]
                analysis["strategy_description"] = content

                # Extract claims
                if "ROI" in content or "return" in content.lower():
                    analysis["edge_claims"].append("Claims ROI/return metrics")
                if "edge" in content.lower():
                    analysis["edge_claims"].append("Mentions edge")
                if "backtest" in content.lower():
                    analysis["edge_claims"].append("Has backtest results")
                if "live" in content.lower():
                    analysis["edge_claims"].append("Claims live trading")
        except:
            pass

    # Check requirements.txt / package.json for tech stack
    req_file = repo_path / "requirements.txt"
    if req_file.exists():
        try:
            with open(req_file) as f:
                analysis["tech_stack"].extend(f.read().split("\n")[:5])
        except:
            pass

    pkg_file = repo_path / "package.json"
    if pkg_file.exists():
        try:
            with open(pkg_file) as f:
                data = json.load(f)
                analysis["tech_stack"].extend(list(data.get("dependencies", {}).keys())[:5])
        except:
            pass

    return analysis

def main():
    print(f"\n{'='*70}")
    print("TESTING TOP 5 GITHUB POLYMARKET BOTS")
    print(f"{'='*70}\n")

    TEMP_DIR.mkdir(exist_ok=True)

    results = {
        "timestamp": datetime.now().isoformat(),
        "bots_tested": len(BOTS_TO_TEST),
        "results": []
    }

    for bot in BOTS_TO_TEST:
        print(f"\n[{bot['type'].upper()}] {bot['name']}")
        print(f"  URL: {bot['url']}")

        # Clone
        repo_path = clone_repo(bot['url'], bot['name'])

        # Analyze
        analysis = analyze_repo(repo_path, bot['name'], bot['type'])
        results["results"].append(analysis)

        # Print summary
        print(f"  Status: {analysis['status']}")
        print(f"  Edge claims: {', '.join(analysis['edge_claims']) if analysis['edge_claims'] else 'None stated'}")
        print(f"  Tech stack: {', '.join(analysis['tech_stack'][:3]) if analysis['tech_stack'] else 'Unknown'}")

    # Save results
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n{'='*70}")
    print(f"✓ Results saved to {RESULTS_FILE}")
    print(f"✓ Repos cloned to {TEMP_DIR}/")
    print(f"\nNext: Run comparative backtest against these bots")
    print(f"{'='*70}\n")

    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
