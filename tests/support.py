"""Synthetic Git repositories for integration tests."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path


def git(
    repo: Path, *args: str, check: bool = True, input_bytes: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        check=False,
        input=input_bytes,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"git {args!r} failed\n{result.stderr.decode('utf-8', 'replace')}"
        )
    return result


def init_repo(path: Path, *, branch: str = "main") -> None:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", branch)
    git(path, "config", "user.email", "review-test@example.com")
    git(path, "config", "user.name", "Review Test")
    git(path, "config", "commit.gpgsign", "false")
    git(path, "config", "core.autocrlf", "false")
    git(path, "config", "core.symlinks", "false")


def write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A", "--", ".")
    git(repo, "commit", "-m", message)
    return head_sha(repo)


def head_sha(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").stdout.decode().strip()


def blob_sha(repo: Path, commit: str, path: str) -> str:
    return git(repo, "rev-parse", f"{commit}:{path}").stdout.decode().strip()


def index_digest(repo: Path) -> str:
    index = repo / ".git" / "index"
    return hashlib.sha256(index.read_bytes()).hexdigest()


def marker_command(script: Path, marker: Path) -> str:
    """Write a script that records execution and return a Git-config command string.

    The command uses forward slashes. Git for Windows runs ``diff.external`` and
    ``core.fsmonitor`` through a shell that mishandles backslashes.
    """

    script.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    executable = Path(sys.executable).as_posix()
    return f'"{executable}" "{script.as_posix()}"'
