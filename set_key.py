#!/usr/bin/env python3
"""One-time helper: securely prompt for private key and write to .env"""
import getpass, os, re, sys

ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

print("\n=== Polymarket private key setup ===")
print("Paste your MetaMask private key below (input is hidden):")
pk = getpass.getpass("Private key: ").strip()

if not re.fullmatch(r'0x[0-9a-fA-F]{64}', pk):
    print(f"\n❌  Doesn't look right — got {len(pk)} chars, need 66 (0x + 64 hex digits).")
    print("    Make sure you're copying the PRIVATE KEY from MetaMask, not the address.")
    sys.exit(1)

# Read existing .env, replace the private key line
lines = open(ENV).readlines() if os.path.exists(ENV) else []
written = False
out = []
for ln in lines:
    if ln.startswith("POLYMARKET_PRIVATE_KEY="):
        out.append(f"POLYMARKET_PRIVATE_KEY={pk}\n")
        written = True
    else:
        out.append(ln)
if not written:
    out.append(f"POLYMARKET_PRIVATE_KEY={pk}\n")

with open(ENV, "w") as f:
    f.writelines(out)

print(f"\n✅  Private key saved to .env ({len(pk)} chars). Run setup next.")
