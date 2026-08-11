import json
from pathlib import Path

temp_dir = Path("github_bots_temp")
strategies = {}

# Analyze each bot's core files
bot_configs = {
    "IMDEA_Arbitrage": {"main_file": "main.py", "strategy": "complete-set arbitrage detection"},
    "warproxxx_poly_maker": {"main_file": "main.py", "strategy": "market making with spread adjustment"},
    "ent0n29_polybot": {"main_file": "src/main.rs", "strategy": "multi-service execution"},
    "Benjam1nCup_Polymarket_trading_bot_python_V2": {"main_file": "main.py", "strategy": "copy trading + liquidity farming"},
    "skharchikov_polymarket_bot": {"main_file": "src/main.rs", "strategy": "ML ensemble + copy trading"}
}

print("="*70)
print("GITHUB BOTS STRATEGY COMPARISON")
print("="*70 + "\n")

for bot_name, config in bot_configs.items():
    bot_path = temp_dir / bot_name
    if bot_path.exists():
        main_py = bot_path / config["main_file"]
        readme = bot_path / "README.md"
        
        print(f"\n[{bot_name}]")
        print(f"Strategy: {config['strategy']}")
        
        # Check for key files
        files_found = list(bot_path.glob("**/*.py")) + list(bot_path.glob("**/*.rs"))[:5]
        print(f"Code files: {len(files_found)} found")
        
        if readme.exists():
            try:
                with open(readme) as f:
                    content = f.read()[:300]
                    print(f"README snippet: {content[:150]}...")
            except:
                pass
        
        # Check for config files
        config_files = list(bot_path.glob("**/config*.json")) + list(bot_path.glob("**/config*.py"))
        if config_files:
            print(f"Config files: {len(config_files)}")
        
        # Check for backtest/results
        results_files = list(bot_path.glob("**/backtest*.json")) + list(bot_path.glob("**/results*.json"))
        if results_files:
            print(f"Backtest results: {len(results_files)}")

print("\n" + "="*70)
print("NEXT: Use best strategies in your 7-day test")
print("="*70)

