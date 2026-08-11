"""leg_macdsig.py — MACD mean-reversion leg (modular)."""
CONFIG = {"enabled": True, "max_open": 8, "bet_usdc": 1.0}
def load_state(): return {"macdsig": {"open": [], "closed": []}}
def save_state(s): pass
def scan_entries(m, s): return []
def scan_exits(b, p): return []
def board(s): print(f"  [MACDSIG] open=0 closed=0 win=0.0% P&L=$+0.00")
