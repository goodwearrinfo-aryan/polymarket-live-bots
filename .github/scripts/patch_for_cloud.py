#!/usr/bin/env python3
"""
patch_for_cloud.py — CI replacement for sync_from_polymarket.sh.

Copies lawyer.py / lawyer_ui.py from the polymarket repo checkout into the
lawyer-app repo checkout, then reapplies the two cloud-only patches (Ollama ->
sentence-transformers embed swap, DB_PATH fallback) that don't exist in the
local-only source copy. Mirrors sync_from_polymarket.sh exactly — keep the two
in sync if the patch logic ever changes.

Usage: patch_for_cloud.py <polymarket_dir> <lawyer_app_dir>
"""
import os
import re
import shutil
import sys

if len(sys.argv) != 3:
    print("usage: patch_for_cloud.py <polymarket_dir> <lawyer_app_dir>", file=sys.stderr)
    sys.exit(1)

src_dir, dst_dir = sys.argv[1], sys.argv[2]

for name in ("lawyer.py", "lawyer_ui.py"):
    shutil.copy(os.path.join(src_dir, name), os.path.join(dst_dir, name))

path = os.path.join(dst_dir, "lawyer.py")
src = open(path).read()

src = src.replace(
'''_HERE   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(os.path.dirname(_HERE), "lawdb.sqlite")
# Allow override via env
DB_PATH = os.environ.get("LAWDB_PATH", DB_PATH)

OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL      = "all-minilm"''',
'''_HERE   = os.path.dirname(os.path.abspath(__file__))
# In cloud the DB lives next to this file; locally it was one level up
_default_db = os.path.join(_HERE, "lawdb.sqlite")
if not os.path.isfile(_default_db):
    _default_db = os.path.join(os.path.dirname(_HERE), "lawdb.sqlite")
DB_PATH = os.environ.get("LAWDB_PATH", _default_db)''')

src = re.sub(
    r'def _embed\(text: str\) -> list\[float\]:\n    """Call Ollama.*?\n(?:.*\n)*?        \) from e\n',
    '''def _embed(text: str) -> list[float]:
    """Embed text using sentence-transformers (cloud) or Ollama (local fallback)."""
    sys.path.insert(0, _HERE)
    from embed import embed as _embed_fn
    return _embed_fn(text)
''',
    src,
)

open(path, 'w').write(src)
print("patched", path)

import ast
for name in ("lawyer.py", "lawyer_ui.py"):
    p = os.path.join(dst_dir, name)
    ast.parse(open(p).read())
    print(f"{name} OK")
