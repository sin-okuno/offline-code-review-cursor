"""Hardening checks against synthetic repositories. No company source is used."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from offline_review import __version__
from offline_review.cli import main
from offline_review.git_adapter import GitAdapter, validate_revision
from offline_review.models import GitError, RevisionError
from offline_review.python_indexer import MAX_PYTHON_SOURCE_BYTES, parse_python_source
from offline_review.redaction import redact_secrets
from tests.support import commit_all, git, head_sha, index_digest, init_repo, marker_command, write

TOKEN = "s3cret-token"
REMOTE = f"https://user:{TOKEN}@example.invalid/synthetic.git"


def _lfs(size: int) -> bytes:
    return (
        b"version https://git-lfs.github.com/spec/v1\n"
        b"oid sha256:" + (b"ab" * 32) + b"\n"
        + f"size {size}\n".encode("ascii")
    )


class RedactionTests(unittest.TestCase):
    def test_url_userinfo_bearer_and_private_key_are_removed(self) -> None:
        key = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
        text = (
            f"see {REMOTE} Bearer {TOKEN} secret={TOKEN} password={TOKEN} "
            f"access_token={TOKEN} {key} SECRET_KEY = 'synthetic-django-key'"
        )
        redacted = redact_secrets(text)
        self.assertNotIn(TOKEN, redacted)
        self.assertNotIn("BEGIN PRIVATE KEY", redacted)
        self.assertIn("SECRET_KEY", redacted)
        self.assertIn("synthetic-django-key", redacted)
        self.assertEqual(redact_secrets(redacted), redacted)

    def test_syntax_excerpt_does_not_keep_url_userinfo(self) -> None:
        source = f"def bad({REMOTE}):\n    return None\n".encode()
        parsed = parse_python_source(source)
        self.assertEqual(parsed.status, "syntax_error")
        self.assertNotIn(TOKEN, parsed.error or "")


class GitHardeningTests(unittest.TestCase):
    def test_transport_revision_never_reaches_git(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            init_repo(repo)
            write(repo / "f.txt", b"a\n")
            commit_all(repo, "base")
            with GitAdapter(repo) as adapter:
                with self.assertRaises(RevisionError) as caught:
                    adapter.resolve_commit(REMOTE)
                self.assertNotIn(TOKEN, str(caught.exception))
                self.assertEqual(adapter.invocations, [])
                with self.assertRaises(RevisionError):
                    adapter.resolve_commit("ext::python evil.py")
                self.assertEqual(validate_revision("HEAD@{1}"), "HEAD@{1}")

    def test_child_environment_and_stderr_drop_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            init_repo(repo)
            dump = root / "dump.json"
            script = root / "fake_git.py"
            script.write_text(
                "\n".join(
                    [
                        "import json",
                        "import os",
                        "import sys",
                        "from pathlib import Path",
                        f"Path({str(dump)!r}).write_text(json.dumps({{",
                        '    "argv": sys.argv,',
                        '    "github_token": os.environ.get("GITHUB_TOKEN"),',
                        '    "askpass": os.environ.get("GIT_ASKPASS"),',
                        '    "aws": os.environ.get("AWS_SECRET_ACCESS_KEY"),',
                        '    "config_count": os.environ.get("GIT_CONFIG_COUNT"),',
                        '    "prompt": os.environ.get("GIT_TERMINAL_PROMPT"),',
                        '}), encoding="utf-8")',
                        f"sys.stderr.write('fatal: {REMOTE}\\n')",
                        "raise SystemExit(1)",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            if os.name == "nt":
                executable = root / "fake_git.cmd"
                executable.write_text(
                    f'@echo off\r\n"{Path(sys.executable)}" "{script}" %*\r\n',
                    encoding="ascii",
                )
            else:
                script.write_text("#!" + sys.executable + "\n" + script.read_text(encoding="utf-8"), encoding="utf-8")
                script.chmod(0o755)
                executable = script
            keys = {
                "GITHUB_TOKEN": "synthetic-token-not-real",
                "GIT_ASKPASS": "synthetic-askpass",
                "AWS_SECRET_ACCESS_KEY": "synthetic-aws",
            }
            previous = {key: os.environ.get(key) for key in keys}
            os.environ.update(keys)
            try:
                with GitAdapter(repo, git_executable=str(executable)) as adapter:
                    with self.assertRaises(GitError) as caught:
                        adapter._run(["rev-parse", "--verify", "HEAD"])
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            message = str(caught.exception)
            self.assertNotIn(TOKEN, message)
            self.assertNotIn("synthetic-token-not-real", message)
            self.assertIn("https://***@example.invalid/synthetic.git", message)
            recorded = json.loads(dump.read_text(encoding="utf-8"))
            self.assertIsNone(recorded["github_token"])
            self.assertIsNone(recorded["askpass"])
            self.assertIsNone(recorded["aws"])
            self.assertEqual(recorded["config_count"], "0")
            self.assertEqual(recorded["prompt"], "0")
            self.assertIn("rev-parse", recorded["argv"])
            self.assertEqual(adapter.invocations[0][0], str(executable))
            self.assertEqual(adapter.invocations[0][1], "-C")

    def test_overrides_disable_fsmonitor_external_diff_and_attribute_driver(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            marker = root / "marker.txt"
            init_repo(repo)
            write(repo / ".gitattributes", b"*.txt diff=evil\n")
            write(repo / "f.txt", b"a\n")
            commit_all(repo, "base")
            base = head_sha(repo)
            write(repo / "f.txt", b"b\n")
            commit_all(repo, "head")
            head = head_sha(repo)
            command = marker_command(root / "evil.py", marker)
            git(repo, "config", "core.fsmonitor", command)
            git(repo, "status", "--porcelain", check=False)
            self.assertTrue(marker.exists(), "the fixture could not prove that core.fsmonitor runs")
            marker.unlink()
            git(repo, "config", "diff.external", command)
            git(repo, "config", "diff.evil.command", command)
            git(repo, "diff", "--no-color", base, head, check=False)
            self.assertTrue(
                marker.exists(),
                "the fixture could not prove that the attribute diff driver or diff.external runs",
            )
            marker.unlink()
            with GitAdapter(repo) as adapter:
                raw, notice = adapter.diff_raw(base, head)
                patch, _patch_notice = adapter.diff_patch(base, head)
                self.assertFalse(adapter.worktree_differs_from_head())
            self.assertFalse(marker.exists())
            self.assertNotIn(TOKEN, notice)
            self.assertIn(b"f.txt", raw)
            self.assertIn("-a", patch)
            self.assertIn("+b", patch)
            diff_commands = [
                item
                for item in adapter.invocations
                if "diff" in item and ("--raw" in item or "--patch" in item or "HEAD" in item)
            ]
            self.assertGreaterEqual(len(diff_commands), 2)
            for item in diff_commands:
                self.assertIn("--no-ext-diff", item)
                self.assertIn("--no-textconv", item)
                self.assertIn("credential.helper=", item)
                self.assertIn("alias.diff=", item)
                self.assertIn("core.fsmonitor=", item)
                self.assertIn("protocol.allow=never", item)
                joined = " ".join(item)
                self.assertNotIn(str(repo / ".git" / "hooks"), joined)

    def test_output_junction_and_report_symlink_do_not_modify_the_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            init_repo(repo)
            write(repo / "pkg" / "app.py", b"alpha\n")
            commit_all(repo, "base")
            base = head_sha(repo)
            write(repo / "pkg" / "app.py", b"beta\n")
            commit_all(repo, "head")
            nested = repo / "nested"
            nested.mkdir()
            sentinel = nested / "sentinel.txt"
            sentinel.write_bytes(b"sentinel")
            link = root / "linked-output"
            _link_directory(link, nested)
            code, stderr = _run_analyze(repo, base, "HEAD", link)
            self.assertEqual(code, 2, stderr)
            self.assertIn("outside the repository", stderr)
            self.assertEqual(sentinel.read_bytes(), b"sentinel")
            self.assertFalse((nested / "review-context.json").exists())

            output = root / "plain-output"
            output.mkdir()
            report_link = output / "review-context.json"
            _link_directory(report_link, nested)
            before = sentinel.read_bytes()
            code, stderr = _run_analyze(repo, base, "HEAD", output)
            self.assertEqual(code, 2, stderr)
            self.assertIn("symlink", stderr)
            self.assertEqual(sentinel.read_bytes(), before)
            self.assertFalse((nested / "review-context.md").exists())


class AngularDrfAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "orders"
        self.output = self.root / "review-out"
        self.marker = self.root / "executed-marker.txt"
        init_repo(self.repo, branch="develop")
        self._write_tree()
        commit_all(self.repo, "scaffold")
        submodule = self.repo / "vendor" / "ui"
        init_repo(submodule)
        write(submodule / "readme.txt", b"ui\n")
        commit_all(submodule, "ui base")
        self.gitlink_old = head_sha(submodule)
        git(
            self.repo,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{self.gitlink_old},vendor/ui",
        )
        git(self.repo, "commit", "-m", "gitlink")
        self.base = head_sha(self.repo)
        self._write_head()
        git(self.repo, "rm", "--", "backend/orders/legacy.py")
        git(self.repo, "mv", "--", "backend/orders/pricing.py", "backend/orders/totals.py")
        write(self.repo / "backend" / "orders" / "totals.py", _TOTALS_HEAD)
        write(submodule / "readme.txt", b"ui-head\n")
        commit_all(submodule, "ui head")
        self.gitlink_new = head_sha(submodule)
        git(
            self.repo,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{self.gitlink_new},vendor/ui",
        )
        git(self.repo, "add", "-A", "--", ".")
        git(self.repo, "commit", "-m", "head")
        self.head = head_sha(self.repo)
        command = marker_command(self.root / "evil.py", self.marker)
        git(self.repo, "config", "core.fsmonitor", command)
        git(self.repo, "status", "--porcelain", check=False)
        if not self.marker.exists():
            raise AssertionError("the fixture could not prove that core.fsmonitor runs")
        self.marker.unlink()
        git(self.repo, "config", "diff.external", command)
        git(self.repo, "config", "credential.helper", command)
        git(self.repo, "config", "filter.lfs.process", command)
        git(self.repo, "config", "filter.lfs.required", "true")
        git(self.repo, "config", "alias.diff", f"!{command}")
        git(self.repo, "config", "remote.origin.url", REMOTE)
        hooks = self.repo / ".git" / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "pre-commit").write_text(f"#!/bin/sh\n{command}\n", encoding="utf-8")
        (hooks / "reference-transaction").write_text(f"#!/bin/sh\n{command}\n", encoding="utf-8")
        git(self.repo, "diff", "--no-color", self.base, self.head, check=False)
        if not self.marker.exists():
            raise AssertionError("the fixture could not prove that diff.external runs")
        self.marker.unlink()
        self.index_before = index_digest(self.repo)
        self.submodule_index_before = index_digest(submodule)
        self.manage_before = (self.repo / "backend" / "manage.py").read_bytes()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_cli_smoke_on_synthetic_angular_drf_repository(self) -> None:
        code, stderr = _run_analyze(
            self.repo,
            self.base,
            "develop",
            self.output,
            "direct",
            python_root="backend",
        )
        self.assertEqual(code, 0, stderr)
        self.assertFalse(self.marker.exists())
        self.assertEqual(index_digest(self.repo), self.index_before)
        self.assertEqual(index_digest(self.repo / "vendor" / "ui"), self.submodule_index_before)
        self.assertEqual((self.repo / "backend" / "manage.py").read_bytes(), self.manage_before)
        first = (self.output / "review-context.json").read_bytes()
        markdown = (self.output / "review-context.md").read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, first.decode("utf-8") + markdown + stderr)
        self.assertNotIn("synthetic-django-key", first.decode("utf-8") + markdown)
        code, stderr = _run_analyze(
            self.repo,
            self.base,
            "develop",
            self.output,
            "direct",
            python_root="backend",
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual((self.output / "review-context.json").read_bytes(), first)
        self.assertFalse(self.marker.exists())

        merge_output = self.root / "merge-out"
        code, stderr = _run_analyze(
            self.repo,
            self.base,
            "develop",
            merge_output,
            "merge-base",
            python_root="backend",
        )
        self.assertEqual(code, 0, stderr)
        merge_report = json.loads((merge_output / "review-context.json").read_text(encoding="utf-8"))
        report = json.loads(first.decode("utf-8"))
        self.assertEqual(merge_report["comparison"]["diff_base_commit"], report["comparison"]["diff_base_commit"])
        self.assertEqual(report["schema_version"], "1.1.0")
        self.assertEqual(report["tool"]["phase"], "2-python")
        self.assertEqual(report["tool"]["version"], __version__)
        self.assertEqual(report["comparison"]["requested_head"], "develop")
        self.assertEqual(report["comparison"]["head_commit"], self.head)
        self.assertEqual(report["python_roots"], ["backend"])
        self.assertFalse(report["coverage"]["analysis_complete"])
        self.assertEqual(report["coverage"]["angular_semantic"], "out_of_scope")
        reasons = set(report["coverage"]["partial_reasons"])
        for reason in (
            "angular_semantic_out_of_scope",
            "binary_files",
            "lfs_pointers_not_smudged",
            "python_parse_incomplete",
            "submodules_not_traversed",
            "unresolved_imports",
            "uncertain_imports",
        ):
            self.assertIn(reason, reasons)

        by_key = {(item["old_path"], item["new_path"]): item for item in report["files"]}
        serializers = by_key[("backend/orders/serializers.py", "backend/orders/serializers.py")]
        self.assertEqual(serializers["status_code"], "M")
        self.assertIn(
            "OrderSerializer.validate_total",
            [item["qualname"] for item in serializers["new_affected_symbols"]],
        )
        legacy = by_key[("backend/orders/legacy.py", None)]
        self.assertEqual(legacy["status"], "deleted")
        self.assertEqual(legacy["new_changed_lines"], [])
        self.assertIn("retired", [item["qualname"] for item in legacy["old_affected_symbols"]])
        renamed = by_key[("backend/orders/pricing.py", "backend/orders/totals.py")]
        self.assertEqual(renamed["status_code"], "R")
        self.assertIn("quote", [item["qualname"] for item in renamed["new_affected_symbols"]])
        self.assertTrue(by_key[("assets/logo.bin", "assets/logo.bin")]["binary"])
        self.assertEqual(by_key[("assets/logo.bin", "assets/logo.bin")]["hunks"], [])
        pointer = by_key[("media/clip.lfs", "media/clip.lfs")]
        self.assertTrue(pointer["lfs_pointer_old"])
        self.assertTrue(pointer["lfs_pointer_new"])
        submodule = by_key[("vendor/ui", "vendor/ui")]
        self.assertTrue(submodule["submodule"])
        self.assertEqual(submodule["old_blob"], self.gitlink_old)
        self.assertEqual(submodule["new_blob"], self.gitlink_new)
        component = by_key[(
            "frontend/src/app/app.component.ts",
            "frontend/src/app/app.component.ts",
        )]
        self.assertEqual(component["new_python_status"], "not_python")
        self.assertNotEqual(component["new_changed_lines"], [])
        self.assertIn(("notes/ü.txt", "notes/ü.txt"), by_key)
        self.assertIn(("notes/release notes.txt", "notes/release notes.txt"), by_key)
        broken = by_key[(None, "backend/orders/broken.py")]
        self.assertEqual(broken["new_python_status"], "syntax_error")
        self.assertEqual(broken["new_affected_symbols"], [])
        huge = by_key[(None, "backend/orders/huge.py")]
        self.assertEqual(huge["new_python_status"], "too_large")
        self.assertEqual(huge["new_affected_symbols"], [])
        health = by_key[(None, "backend/orders/health.py")]
        self.assertIn("ping", [item["qualname"] for item in health["new_affected_symbols"]])

        relationships = report["import_relationships"]
        self.assertTrue(
            any(
                item["certainty"] == "confirmed"
                and item["other_module"] == "orders.models"
                and item["other_path"] == "backend/orders/models.py"
                for item in relationships
            )
        )
        self.assertTrue(
            any(
                item["certainty"] == "unresolved" and item["other_module"] == "rest_framework"
                for item in relationships
            )
        )
        self.assertTrue(
            any(
                item["direction"] == "incoming"
                and item["changed_path"] == "backend/orders/views.py"
                and item["other_path"] == "backend/orders/urls.py"
                for item in relationships
            )
        )
        self.assertTrue(
            any(
                item["direction"] == "outgoing"
                and item["certainty"] == "confirmed"
                and item["changed_path"] == "backend/orders/health.py"
                and item["other_path"] == "backend/orders/generated/m00.py"
                for item in relationships
            )
        )
        codes = {item["code"] for item in report["warnings"]}
        for code_name in (
            "ANGULAR_SEMANTIC_OUT_OF_SCOPE",
            "BINARY_FILE",
            "LFS_POINTER",
            "PYTHON_FILE_TOO_LARGE",
            "PYTHON_SYNTAX_ERROR",
            "SUBMODULE",
        ):
            self.assertIn(code_name, codes)
        self.assertNotIn("WORKTREE_DIRTY", codes)
        self.assertIn("Angular and TypeScript type-checker analysis is out of scope", markdown)

        write(self.repo / "backend" / "orders" / "serializers.py", _SERIALIZERS_HEAD + b"DIRTY-WORKTREE-LINE\n")
        write(self.repo / "UNTRACKED.py", b"UNTRACKED-LINE\n")
        dirty_output = self.root / "dirty-out"
        code, stderr = _run_analyze(
            self.repo,
            self.base,
            "develop",
            dirty_output,
            "direct",
            python_root="backend",
        )
        self.assertEqual(code, 0, stderr)
        dirty_text = (dirty_output / "review-context.json").read_text(encoding="utf-8")
        dirty = json.loads(dirty_text)
        self.assertIn("WORKTREE_DIRTY", {item["code"] for item in dirty["warnings"]})
        self.assertNotIn("DIRTY-WORKTREE-LINE", dirty_text)
        self.assertNotIn("UNTRACKED-LINE", dirty_text)
        self.assertNotIn("UNTRACKED.py", dirty_text)
        dirty_serializers = next(
            item
            for item in dirty["files"]
            if item["new_path"] == "backend/orders/serializers.py"
        )
        self.assertEqual(dirty_serializers["new_affected_symbols"], serializers["new_affected_symbols"])
        self.assertEqual(index_digest(self.repo), self.index_before)
        self.assertFalse(self.marker.exists())
        self.assertTrue((self.repo / "UNTRACKED.py").exists())

    def _write_tree(self) -> None:
        write(self.repo / "backend" / "manage.py", _manage(self.marker))
        write(self.repo / "backend" / "config" / "__init__.py", b"")
        write(self.repo / "backend" / "config" / "settings.py", _SETTINGS)
        write(self.repo / "backend" / "orders" / "__init__.py", b"")
        write(self.repo / "backend" / "orders" / "models.py", _MODELS)
        write(self.repo / "backend" / "orders" / "serializers.py", _SERIALIZERS_BASE)
        write(self.repo / "backend" / "orders" / "views.py", _VIEWS_BASE)
        write(self.repo / "backend" / "orders" / "urls.py", _URLS)
        write(self.repo / "backend" / "orders" / "legacy.py", _LEGACY)
        write(self.repo / "backend" / "orders" / "pricing.py", _TOTALS_BASE)
        write(self.repo / "backend" / "orders" / "migrations" / "__init__.py", b"")
        write(self.repo / "backend" / "orders" / "migrations" / "0001_initial.py", _MIGRATION)
        write(self.repo / "backend" / "orders" / "generated" / "__init__.py", b"")
        for index in range(20):
            write(
                self.repo / "backend" / "orders" / "generated" / f"m{index:02d}.py",
                f"value = {index}\n".encode(),
            )
        write(self.repo / "frontend" / "package.json", _PACKAGE)
        write(self.repo / "frontend" / "angular.json", _ANGULAR)
        write(self.repo / "frontend" / "src" / "app" / "app.component.ts", _COMPONENT_BASE)
        write(self.repo / "frontend" / "src" / "app" / "app.component.html", b"<h1>{{ title }}</h1>\n")
        write(self.repo / "frontend" / "src" / "app" / "app.component.scss", b"h1 { color: black; }\n")
        write(self.repo / "frontend" / "src" / "app" / "orders.service.ts", _SERVICE)
        write(self.repo / "notes" / "release notes.txt", b"base\n")
        write(self.repo / "notes" / "ü.txt", "基底\n".encode())
        write(self.repo / "assets" / "logo.bin", b"\x00\x01\xff")
        write(self.repo / "media" / "clip.lfs", _lfs(4))

    def _write_head(self) -> None:
        write(self.repo / "backend" / "orders" / "serializers.py", _SERIALIZERS_HEAD)
        write(self.repo / "backend" / "orders" / "views.py", _VIEWS_HEAD)
        write(self.repo / "backend" / "orders" / "broken.py", b"def broken(:\n    return None\n")
        write(self.repo / "backend" / "orders" / "health.py", _HEALTH)
        write(
            self.repo / "backend" / "orders" / "huge.py",
            b"\x00" + (b"x" * MAX_PYTHON_SOURCE_BYTES),
        )
        write(self.repo / "frontend" / "src" / "app" / "app.component.ts", _COMPONENT_HEAD)
        write(self.repo / "frontend" / "src" / "app" / "app.component.html", b"<h1>{{ title }} changed</h1>\n")
        write(self.repo / "frontend" / "src" / "app" / "app.component.scss", b"h1 { color: navy; }\n")
        write(self.repo / "notes" / "release notes.txt", b"head\n")
        write(self.repo / "notes" / "ü.txt", "更新\n".encode())
        write(self.repo / "assets" / "logo.bin", b"\x00\x02\xff")
        write(self.repo / "media" / "clip.lfs", _lfs(8))


def _link_directory(link: Path, target: Path) -> None:
    if os.name == "nt":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if created.returncode != 0:
            raise AssertionError(created.stderr)
        return
    link.symlink_to(target, target_is_directory=True)


def _run_analyze(
    repo: Path,
    base: str,
    head: str,
    output: Path,
    comparison: str = "direct",
    python_root: str | None = None,
) -> tuple[int, str]:
    argv = [
        "analyze",
        "--repo",
        str(repo),
        "--base",
        base,
        "--head",
        head,
        "--output",
        str(output),
        "--comparison",
        comparison,
    ]
    if python_root is not None:
        argv.extend(["--python-root", python_root])
    stderr = StringIO()
    with redirect_stdout(StringIO()), redirect_stderr(stderr):
        code = main(argv)
    return code, stderr.getvalue()


def _manage(marker: Path) -> bytes:
    return (
        "from pathlib import Path\n\n"
        "def main() -> None:\n"
        f"    Path({str(marker)!r}).write_text('executed-manage', encoding='utf-8')\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    ).encode()


_SETTINGS = b'SECRET_KEY = "synthetic-django-key"\n\nINSTALLED_APPS = ["orders"]\n'
_MODELS = (
    b"class Order:\n"
    b"    def __init__(self, total: int) -> None:\n"
    b"        self.total = total\n"
)
_SERIALIZERS_BASE = (
    b"from rest_framework import serializers\n\n"
    b"from .models import Order\n\n\n"
    b"class OrderSerializer(serializers.Serializer):\n"
    b"    def validate_total(self, value: int) -> int:\n"
    b"        if value < 0:\n"
    b"            raise ValueError('negative')\n"
    b"        return value\n"
)
_SERIALIZERS_HEAD = _SERIALIZERS_BASE.replace(b"value < 0", b"value <= 0")
_VIEWS_BASE = (
    b"from rest_framework.views import APIView\n\n"
    b"from .serializers import OrderSerializer\n\n\n"
    b"class OrderView(APIView):\n"
    b"    def get(self, request: object) -> dict[str, bool]:\n"
    b"        return {'ok': True}\n"
)
_VIEWS_HEAD = _VIEWS_BASE.replace(b"{'ok': True}", b"{'ok': False}")
_URLS = (
    b"from django.urls import path\n\n"
    b"from .views import OrderView\n\n"
    b"urlpatterns = [path('orders/', OrderView.as_view())]\n"
)
_LEGACY = b"def retired() -> None:\n    return None\n"
_TOTALS_BASE = b"def quote(total: int) -> int:\n    return total\n"
_TOTALS_HEAD = b"def quote(total: int) -> int:\n    return total + 1\n"
_MIGRATION = b"def forwards() -> None:\n    return None\n"
_HEALTH = (
    b"import importlib\n\n"
    b"from orders.generated.m00 import value\n\n\n"
    b"def ping() -> int:\n"
    b"    importlib.import_module('orders.models')\n"
    b"    return value\n"
)
_COMPONENT_BASE = (
    b"import { Component } from '@angular/core';\n\n"
    b"@Component({\n"
    b"  selector: 'app-root',\n"
    b"  templateUrl: './app.component.html',\n"
    b"  styleUrls: ['./app.component.scss'],\n"
    b"})\n"
    b"export class AppComponent {\n"
    b"  title = 'orders';\n"
    b"}\n"
)
_COMPONENT_HEAD = _COMPONENT_BASE.replace(b"title = 'orders';", b"title = 'orders-review';")
_SERVICE = b"export class OrdersService {\n  list(): string[] {\n    return [];\n  }\n}\n"
_PACKAGE = b'{"name":"synthetic-orders","private":true,"scripts":{"build":"echo do-not-run"}}\n'
_ANGULAR = b'{"version":1,"projects":{"orders":{"projectType":"application"}}}\n'
