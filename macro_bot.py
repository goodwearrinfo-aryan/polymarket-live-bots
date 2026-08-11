"""
macro_bot.py — standalone macro/political paper-trading bot.

Runs macro + political legs:
  - truefade (fade YES on macro/politics [0.22,0.52], ≤30d)
  - quietfade (fade when news quiet)
  - [future: macro covariate-driven, geo-political]

Independent trading on macro questions.

Usage:
  python3 macro_bot.py board
"""
MACRO_LEGS = ["truefade", "quietfade", "nearresfade"]

def board():
    print("\n" + "="*60)
    print("  MACRO BOT — paper trading (macro + political legs)")
    print("="*60)
    for leg in MACRO_LEGS:
        print(f"  [{leg.upper():15s}] open=0 closed=0 win=0.0% P&L=$+0.00")
    print(f"\n  TOTAL: open=0 closed=0 P&L=$+0.00")

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "board"
    if cmd == "board":
        board()
