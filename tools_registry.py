#!/usr/bin/env python3
"""
tools_registry.py — auto-discovers every public function in lawyer_agents/*.py and
exposes them as a uniform {key, label, params} registry + a single call(key, kwargs)
dispatcher, so the UI/API never needs a hand-written form per agent.

list_tools() -> [{key, module, func, label, params: [{name, required, default}]}]
call(key, kwargs) -> whatever the underlying function returns (dict/str/etc.)

Param values arrive as strings from an HTML form; call() tries json.loads on each
value first (so "50000" -> int, '{"a":1}' -> dict, "true" -> bool) and falls back
to the raw string if it isn't valid JSON — this covers every param shape in the
lawyer_agents functions (numbers, dicts, plain text) without per-agent UI code.
"""
import ast
import importlib
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENTS_DIR = os.path.join(_HERE, "lawyer_agents")

_SKIP_FUNCS = {"interactive", "main"}


def _inspect_module_functions(path):
    """AST-parse a module file for public top-level function signatures without
    importing it (avoids import-time side effects across all 27 files)."""
    tree = ast.parse(open(path).read())
    funcs = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name.startswith("_") or node.name in _SKIP_FUNCS:
            continue
        args = [a.arg for a in node.args.args]
        ndef = len(node.args.defaults)
        required = args[: len(args) - ndef] if ndef else args
        doc = ast.get_docstring(node) or ""
        params = []
        for a in args:
            params.append({"name": a, "required": a in required})
        funcs.append({"name": node.name, "params": params, "doc": doc.splitlines()[0] if doc else ""})
    return funcs


def list_tools():
    """Discover every module in lawyer_agents/ and its public functions."""
    tools = []
    for fname in sorted(os.listdir(_AGENTS_DIR)):
        if not fname.endswith(".py") or fname == "__init__.py":
            continue
        modname = fname[:-3]
        path = os.path.join(_AGENTS_DIR, fname)
        try:
            funcs = _inspect_module_functions(path)
        except SyntaxError:
            continue
        for f in funcs:
            tools.append({
                "key": f"{modname}.{f['name']}",
                "module": modname,
                "func": f["name"],
                "label": f"{modname.replace('_', ' ').title()} — {f['name'].replace('_', ' ')}",
                "doc": f["doc"],
                "params": f["params"],
            })
    return tools


def _coerce(value):
    """Try JSON first (numbers/bools/dicts/lists), fall back to the raw string."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return value


def call(key, kwargs):
    """Call one registered tool by 'module.func' key with a dict of raw (usually
    string) param values. Returns the function's raw return value."""
    if "." not in key:
        raise ValueError(f"bad tool key: {key!r}")
    modname, funcname = key.split(".", 1)
    valid_keys = {t["key"] for t in list_tools()}
    if key not in valid_keys:
        raise ValueError(f"unknown tool: {key!r}")

    mod = importlib.import_module(f"lawyer_agents.{modname}")
    fn = getattr(mod, funcname)
    coerced = {k: _coerce(v) for k, v in (kwargs or {}).items() if v not in (None, "")}
    return fn(**coerced)
