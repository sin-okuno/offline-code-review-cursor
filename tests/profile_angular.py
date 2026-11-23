"""Measure Angular indexing on a synthetic repository of about 100,000 lines.

unittest does not collect this module. From the workspace root:

    $env:PYTHONPATH = "src"
    python tests/profile_angular.py

The repository is temporary. Peak RSS is the Windows working-set peak of the
measurement process, or VmHWM on Linux. Wall-clock time wraps only the measured
calls. The fixture is generated text, not a real application.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

FEATURES = 200
METHODS = 420
HTML_LINES = 40


def peak_rss_bytes() -> int:
    if os.name == "nt":
        class MemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_uint32),
                ("PageFaultCount", ctypes.c_uint32),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = MemoryCounters()
        counters.cb = ctypes.sizeof(MemoryCounters)
        query = ctypes.windll.kernel32.K32GetProcessMemoryInfo
        query.argtypes = [ctypes.c_void_p, ctypes.POINTER(MemoryCounters), ctypes.c_uint32]
        query.restype = ctypes.c_int
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if not query(handle, ctypes.byref(counters), counters.cb):
            raise SystemExit(f"K32GetProcessMemoryInfo failed: {ctypes.get_last_error()}")
        return int(counters.PeakWorkingSetSize)
    status = Path("/proc/self/status").read_text(encoding="utf-8")
    for line in status.splitlines():
        if line.startswith("VmHWM:"):
            return int(line.split()[1]) * 1024
    raise SystemExit("VmHWM was not available")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    return result.stdout.decode().strip()


def _feature(index: int) -> str:
    return f"feature-{index:03d}"


def _component(index: int, *, touched: bool) -> str:
    name = _feature(index)
    previous = ""
    if index:
        previous = (
            f"import {{ Feature{index - 1:03d}Service }} from "
            f"'../{_feature(index - 1)}/feature.service';\n"
        )
    methods = "".join(f"  value{line}(): number {{ return {line}; }}\n" for line in range(METHODS))
    extra = "  touched(): number { return 1; }\n" if touched else ""
    return (
        "import { Component } from '@angular/core';\n"
        f"import {{ Feature{index:03d}Service }} from './feature.service';\n"
        f"{previous}"
        "\n"
        "@Component({\n"
        f"  selector: 'app-{name}',\n"
        "  templateUrl: './feature.component.html',\n"
        "  standalone: true,\n"
        "})\n"
        f"export class Feature{index:03d}Component {{\n"
        f"{methods}{extra}"
        "}\n"
    )


def _html() -> str:
    return "".join(f"<p>line {line}</p>\n" for line in range(HTML_LINES))


def _service(index: int) -> str:
    return (
        f"export class Feature{index:03d}Service {{\n"
        "  ready(): boolean { return true; }\n"
        "}\n"
    )


def _actions(index: int) -> str:
    return (
        "import { createAction } from '@ngrx/store';\n"
        "\n"
        f"export const loadFeature{index:03d} = createAction('[Feature{index:03d}] Load');\n"
    )


def _effects(index: int) -> str:
    return (
        "import { ofType } from '@ngrx/effects';\n"
        f"import {{ loadFeature{index:03d} }} from './feature.actions';\n"
        "\n"
        f"export const loadFeature{index:03d}Effect = createEffect(() => ofType(loadFeature{index:03d}));\n"
    )


def _reducer(index: int) -> str:
    return (
        "import { createReducer, on } from '@ngrx/store';\n"
        f"import {{ loadFeature{index:03d} }} from './feature.actions';\n"
        "\n"
        "export const featureReducer = createReducer({}, on(loadFeature"
        f"{index:03d}, (state) => state));\n"
    )


def generate(repo: Path) -> tuple[str, int, int]:
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "review-test@example.com")
    _git(repo, "config", "user.name", "Review Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.autocrlf", "false")
    lines = 0
    files = 0
    for index in range(FEATURES):
        folder = repo / "src" / "app" / _feature(index)
        folder.mkdir(parents=True)
        written = {
            "feature.component.ts": _component(index, touched=False),
            "feature.component.html": _html(),
            "feature.service.ts": _service(index),
            "feature.actions.ts": _actions(index),
            "feature.effects.ts": _effects(index),
            "feature.reducer.ts": _reducer(index),
        }
        for name, text in written.items():
            (folder / name).write_text(text, encoding="utf-8", newline="\n")
            lines += text.count("\n")
            files += 1
    _git(repo, "add", "-A", "--", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    changed = repo / "src" / "app" / "feature-000" / "feature.component.ts"
    changed.write_text(_component(0, touched=True), encoding="utf-8", newline="\n")
    _git(repo, "add", "-A", "--", ".")
    _git(repo, "commit", "-m", "head")
    return base, lines, files


def measure_phases(repo: Path, base: str, cache_dir: Path | None) -> None:
    from offline_review.dependency_graph import build_angular_graph, walk_forward
    from offline_review.git_adapter import GitAdapter
    from offline_review.typescript_cache import TypeScriptCache
    from offline_review.typescript_index import index_typescript_commit

    cache = TypeScriptCache(cache_dir) if cache_dir is not None else None
    with GitAdapter(repo) as adapter:
        head = adapter.resolve_commit("HEAD")
        diff_base = adapter.resolve_commit(base)
        started = time.perf_counter()
        base_index = index_typescript_commit(adapter, diff_base, cache)
        head_index = index_typescript_commit(adapter, head, cache)
        index_seconds = time.perf_counter() - started
        started = time.perf_counter()
        base_graph = build_angular_graph(base_index)
        head_graph = build_angular_graph(head_index)
        graph_seconds = time.perf_counter() - started
        started = time.perf_counter()
        walked = walk_forward(head_graph, "src/app/feature-199/feature.component.ts", max_depth=8)
        walk_seconds = time.perf_counter() - started
        invocations = list(adapter.invocations)
    print(f"INDEX_SEC {index_seconds:.3f}")
    print(f"GRAPH_SEC {graph_seconds:.3f}")
    print(f"WALK_SEC {walk_seconds:.3f}")
    print(f"INDEX_FILES {len(base_index.files) + len(head_index.files)}")
    print(f"INDEX_HTML {len(base_index.html_files) + len(head_index.html_files)}")
    print(f"EDGES {len(head_graph.edges)}")
    print(f"WALK_PATHS {len(walked.paths)}")
    print(f"GIT_CALLS {len(invocations)}")
    counts: dict[str, int] = {}
    for command in invocations:
        for arg in command:
            if arg in {"cat-file", "diff", "ls-files", "ls-tree", "merge-base", "rev-parse"}:
                counts[arg] = counts.get(arg, 0) + 1
                break
    print("GIT_COUNTS " + ",".join(f"{name}:{counts[name]}" for name in sorted(counts)))
    print(f"PEAK_RSS_BYTES {peak_rss_bytes()}")


def measure_report(repo: Path, base: str, output: Path, cache: Path | None) -> None:
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
        "--max-depth",
        "2",
        "--max-files",
        "40",
        "--max-bytes",
        "1048576",
        "--max-lines-per-excerpt",
        "200",
    ]
    if cache is not None:
        argv.extend(["--cache", str(cache)])
    started = time.perf_counter()
    code = main(argv)
    elapsed = time.perf_counter() - started
    print(f"EXIT {code}")
    print(f"REPORT_SEC {elapsed:.3f}")
    print(f"PEAK_RSS_BYTES {peak_rss_bytes()}")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "phases":
        cache = None if sys.argv[4] == "-" else Path(sys.argv[4])
        measure_phases(Path(sys.argv[2]), sys.argv[3], cache)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        cache = None if sys.argv[5] == "-" else Path(sys.argv[5])
        measure_report(Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]), cache)
        return
    print(f"MACHINE {platform.platform()}")
    print(f"PYTHON {sys.version.split()[0]}")
    root = Path(tempfile.mkdtemp(prefix="offline-review-angular-"))
    repo = root / "repo"
    started = time.perf_counter()
    base, lines, files = generate(repo)
    print(f"GENERATE_SEC {time.perf_counter() - started:.3f}")
    print(f"LINES {lines}")
    print(f"FILES {files}")
    print(f"FEATURES {FEATURES}")
    phases = _spawn(["phases", str(repo), base, "-"])
    phase_cache = root / "phase-cache"
    phases_fill = _spawn(["phases", str(repo), base, str(phase_cache)])
    phases_hit = _spawn(["phases", str(repo), base, str(phase_cache)])
    first = root / "first"
    cached = root / "cached-out"
    cached_again = root / "cached-again"
    report_cache = root / "report-cache"
    report_one = _spawn(["report", str(repo), base, str(first), "-"])
    report_fill = _spawn(["report", str(repo), base, str(cached), str(report_cache)])
    report_hit = _spawn(["report", str(repo), base, str(cached_again), str(report_cache)])
    one = (first / "review-context.json").read_bytes()
    filled = (cached / "review-context.json").read_bytes()
    hit = (cached_again / "review-context.json").read_bytes()
    payload = json.loads(one.decode("utf-8"))
    angular = payload["angular_context"]
    cache_files = [path for path in report_cache.rglob("*") if path.is_file()]
    print(phases)
    print("PHASES_FILL", phases_fill)
    print("PHASES_HIT", phases_hit)
    print(report_one)
    print("REPORT_FILL", report_fill)
    print("REPORT_HIT", report_hit)
    print("REPORTS_MATCH", one == filled == hit)
    print("CACHE_FILES", len(cache_files))
    print("CACHE_BYTES", sum(path.stat().st_size for path in cache_files))
    print("SCHEMA", payload["schema_version"])
    print("ANALYSIS_COMPLETE", payload["coverage"]["analysis_complete"])
    print("ANGULAR_SEMANTIC", payload["coverage"]["angular_semantic"])
    print("ANGULAR_STATUS", angular["status"])
    print("SELECTED", len(angular["selected"]))
    print("REPORT_EDGES", len(angular["edges"]))
    print("REPORT_BYTES", len(one))
    print("CHANGED_FILES", payload["coverage"]["files_total"])


def _spawn(args: list[str]) -> str:
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr)
    return " ".join(result.stdout.split())


if __name__ == "__main__":
    main()
