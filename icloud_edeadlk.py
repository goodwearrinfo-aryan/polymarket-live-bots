"""icloud_edeadlk — transparently retry file I/O that loses a race with iCloud sync.

Aryan's Mac has Desktop & Documents iCloud sync ON, so ~/Documents/{polymarket,
edge-bots,TradingAgents,PolymarketVault,oss-bots,Codex} are CloudDocs-mediated.
The `bird`/`fileproviderd` sync daemon briefly takes exclusive advisory locks on
files while reconciling, so file I/O that lands inside that window fails with:

    OSError: [Errno 11] Resource deadlock avoided   (errno.EDEADLK)

This is intermittent — most cycles succeed — but it silently exit-1s a shifting
set of ~26 com.aryan.* launchd jobs. See the project memory note
`icloud-sync-errno11-launchd-2026-07-01`.

The lock can strike at TWO moments:
  1. during open() itself                    -> wrapped open() retries the call
  2. AFTER open() succeeds, during the first read()/iterate/write()/flush/close
     (e.g. `json.load(open(p))`, `for line in open(p)`, dotenv `stream.read()`,
     `open(log,"a")` then buffered write flushed at close)  -> the file object's
     data methods must ALSO retry.

So a wrapped open() on a path UNDER an iCloud-synced root returns a thin proxy
whose read/readline/readlines/write/writelines/iteration/flush/close retry on
EDEADLK. Files NOT under ~/Documents are returned RAW and completely unchanged —
the behavioral change is confined to exactly the paths that can deadlock, so
unrelated I/O (/tmp, sockets, site-packages, temp files) is never touched.

DESIGN GUARANTEES (so a bug here can never break the interpreter):
  * install() is fully wrapped in try/except — any failure leaves open() untouched.
  * idempotent — importing/installing twice is a no-op.
  * ONLY errno 11 (EDEADLK) is retried; every other OSError re-raises immediately.
  * bounded retries (~20s worst case), then the original error re-raises honestly —
    nothing is ever masked.
  * the proxy delegates every unknown attribute to the real file object, so
    fileno()/name/mode/encoding/isinstance-on-getattr all still work.

Reverse this fix by deleting icloud_edeadlk.py + icloud_edeadlk.pth from the
interpreter's site-packages.
"""

import builtins
import errno
import io
import os
import time

# Worst-case added latency ≈ sum(_BACKOFFS) ≈ 95s, only on a real EDEADLK. Two
# causes of EDEADLK, both time-bounded and self-clearing on retry:
#   * a brief sync-burst lock (sub-second to a few seconds), and
#   * a COLD read of an iCloud-EVICTED ("dataless") file, where the first access
#     faults an on-demand download that has been observed to take up to ~63s.
# The budget must outlast the slower (materialization) case, so a job reading an
# evicted state/log file STALLS then SUCCEEDS instead of failing exit-1. A warm
# (already-local) file pays ZERO — the first attempt succeeds, no sleep runs.
_BACKOFFS = (0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 2.0, 2.5, 3.0, 3.0, 3.0,
             4.0, 5.0, 5.0, 6.0, 7.0, 8.0, 8.0, 8.0, 8.0)
_SENTINEL = "_icloud_edeadlk_wrapped"

# iCloud-synced roots. Only files under these get the proxy; everything else is raw.
_SYNC_ROOTS = tuple(
    os.path.realpath(os.path.expanduser(p)) for p in ("~/Documents",)
)


def _retry(func):
    """Run func, retrying ONLY on EDEADLK with backoff, else re-raise at once."""
    def wrapper(*args, **kwargs):
        last = None
        for delay in (0.0,) + _BACKOFFS:
            if delay:
                time.sleep(delay)
            try:
                return func(*args, **kwargs)
            except OSError as e:
                if e.errno != errno.EDEADLK:
                    raise
                last = e
        raise last
    setattr(wrapper, _SENTINEL, True)
    return wrapper


def _under_sync_root(path):
    try:
        rp = os.path.realpath(path)
        return any(rp == r or rp.startswith(r + os.sep) for r in _SYNC_ROOTS)
    except Exception:
        return False


class _EDEADLKFile:
    """Thin proxy around a real file object: retries data ops on EDEADLK, delegates
    everything else. Used only for files under an iCloud-synced root."""

    __slots__ = ("_f",)

    def __init__(self, f):
        object.__setattr__(self, "_f", f)

    # --- data methods: EDEADLK-retried ---
    def read(self, *a, **k):        return _retry(self._f.read)(*a, **k)
    def readline(self, *a, **k):    return _retry(self._f.readline)(*a, **k)
    def readlines(self, *a, **k):   return _retry(self._f.readlines)(*a, **k)
    def write(self, *a, **k):       return _retry(self._f.write)(*a, **k)
    def writelines(self, *a, **k):  return _retry(self._f.writelines)(*a, **k)
    def flush(self):                return _retry(self._f.flush)()
    def close(self):                return _retry(self._f.close)()

    # --- iteration: readline under the hood can EDEADLK ---
    def __iter__(self):             return self
    def __next__(self):
        line = _retry(self._f.readline)()
        if line == "" or line == b"":
            raise StopIteration
        return line

    # --- context manager: __exit__ closes (with retry) ---
    def __enter__(self):            return self
    def __exit__(self, *exc):       self.close(); return False

    # --- everything else (fileno, name, mode, seek, tell, encoding, ...) ---
    def __getattr__(self, name):    return getattr(self._f, name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_f"), name, value)


def _make_open(real_open):
    def open_wrapped(file, *args, **kwargs):
        # Retry the open() call itself on EDEADLK.
        f = _retry(real_open)(file, *args, **kwargs)
        # Wrap ONLY real path-based opens under an iCloud-synced root.
        try:
            if not isinstance(file, int) and _under_sync_root(os.fspath(file)):
                return _EDEADLKFile(f)
        except Exception:
            pass
        return f
    setattr(open_wrapped, _SENTINEL, True)
    return open_wrapped


def install():
    """Idempotently patch open() + pathlib helpers + os.replace. Never raises."""
    try:
        if not getattr(builtins.open, _SENTINEL, False):
            builtins.open = _make_open(builtins.open)
        # io.open is the same callable used by pathlib.Path.open / many libs.
        if not getattr(io.open, _SENTINEL, False):
            io.open = builtins.open

        import pathlib
        for name in ("read_text", "write_text", "read_bytes", "write_bytes"):
            orig = getattr(pathlib.Path, name, None)
            if orig is not None and not getattr(orig, _SENTINEL, False):
                setattr(pathlib.Path, name, _retry(orig))

        if not getattr(os.replace, _SENTINEL, False):
            os.replace = _retry(os.replace)
    except Exception:
        # A patch failure must never take down the interpreter or the job.
        pass


install()
