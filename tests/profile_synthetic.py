"""Measure the CLI on a synthetic repository of at least 100,000 Python lines.

unittest does not collect this module. From the workspace root:

    $env:PYTHONPATH = "src"
    python tests/profile_synthetic.py

The repository is temporary. Peak RSS is the Windows working-set peak of the
measurement process, including interpreter startup, read with
K32GetProcessMemoryInfo. Wall-clock time wraps only the analyze call.
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path


FILE_COUNT = 500
LINES_PER_FILE = 200


class _MemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def peak_rss_bytes() -> int:
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(_MemoryCounters)
    query = ctypes.windll.kernel32.K32GetProcessMemoryInfo
    query.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MemoryCounters), wintypes.DWORD]
    query.restype = wintypes.BOOL
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    if not query(handle, ctypes.byref(counters), counters.cb):
        raise SystemExit(f"K32GetProcessMemoryInfo failed: {ctypes.get_last_error()}")
    return int(counters.PeakWorkingSetSize)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True
    )
    return result.stdout.decode().strip()


def generate(repo: Path) -> tuple[str, int]:
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "review-test@example.com")
    _git(repo, "config", "user.name", "Review Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.autocrlf", "false")
    line_count = 0
    for index in range(FILE_COUNT):
        text = "".join(f"n{line} = {line}\n" for line in range(LINES_PER_FILE))
        path = repo / "pkg" / f"m{index:03d}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        line_count += LINES_PER_FILE
    app = "from pkg.m000 import n0\n\n\ndef changed():\n    return n0\n"
    (repo / "pkg" / "app.py").write_text(app, encoding="utf-8", newline="\n")
    line_count += app.count("\n")
    _git(repo, "add", "-A", "--", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "pkg" / "app.py").write_text(
        "from pkg.m000 import n0\n\n\ndef changed():\n    return n0 + 1\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", "-A", "--", ".")
    _git(repo, "commit", "-m", "head")
    return base, line_count


def measure(repo: Path, base: str, output: Path, cache: Path | None) -> None:
    from offline_review.cli import main

    argv = [
        "analyze",
        "--repo",
        str(repo),
        "--base",
        base,
        "--head",
        "HEAD",
        "--output",
        str(output),
        "--comparison",
        "direct",
        "--python-root",
        ".",
    ]
    if cache is not None:
        argv.extend(["--cache", str(cache)])
    started = time.perf_counter()
    code = main(argv)
    elapsed = time.perf_counter() - started
    print(f"EXIT {code}")
    print(f"ELAPSED_SEC {elapsed:.3f}")
    print(f"PEAK_RSS_BYTES {peak_rss_bytes()}")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "measure":
        cache = None if sys.argv[5] == "-" else Path(sys.argv[5])
        measure(Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]), cache)
        return
    root = Path(tempfile.mkdtemp(prefix="offline-review-100k-"))
    repo = root / "repo"
    base, line_count = generate(repo)
    print(f"LINES {line_count}")
    print(f"FILES {FILE_COUNT + 1}")
    cold_out = root / "cold-out"
    warm_out = root / "warm-out"
    cache = root / "cache"
    cold = _run_measure(repo, base, cold_out, None)
    warm = _run_measure(repo, base, warm_out, cache)
    warm_again = _run_measure(repo, base, root / "warm-again", cache)
    cold_bytes = (cold_out / "review-context.json").read_bytes()
    warm_bytes = (warm_out / "review-context.json").read_bytes()
    again_bytes = (root / "warm-again" / "review-context.json").read_bytes()
    report = json.loads(cold_bytes.decode("utf-8"))
    cache_files = list(cache.glob("*.json"))
    print("CACHE_FILES", len(cache_files))
    print("CACHE_BYTES", sum(path.stat().st_size for path in cache_files))
    print("COLD", cold)
    print("WARM_FILL", warm)
    print("WARM_HIT", warm_again)
    print("REPORTS_MATCH", cold_bytes == warm_bytes == again_bytes)
    print("CHANGED_FILES", report["coverage"]["files_total"])
    print("INDEXED", report["coverage"]["python_files_indexed"])
    print("CONFIRMED", report["coverage"]["imports_confirmed"])
    print("SCHEMA", report["schema_version"])


def _run_measure(repo: Path, base: str, output: Path, cache: Path | None) -> str:
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "measure",
            str(repo),
            base,
            str(output),
            "-" if cache is None else str(cache),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr)
    return " ".join(result.stdout.split())


if __name__ == "__main__":
    main()
