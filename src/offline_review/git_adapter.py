"""Read-only Git access for local commit, diff, status, and object reads.

The adapter resolves revisions to commit object IDs before diffing. It never
invokes network transports, hooks, external diff drivers, textconv filters,
LFS smudge filters, or submodule fetches. Those controls are process-local;
they are not an operating-system firewall.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from offline_review.diff_parser import decode_git_path
from offline_review.models import GitError, RevisionError
from offline_review.redaction import redact_secrets

_ALLOWED_SUBCOMMANDS = frozenset(
    {"cat-file", "diff", "ls-files", "ls-tree", "merge-base", "rev-parse"}
)
_BLOCKED_SUBCOMMANDS = frozenset(
    {
        "archive",
        "clone",
        "fetch",
        "ls-remote",
        "pull",
        "push",
        "remote",
        "submodule",
        "upload-pack",
    }
)
_SHA = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MAX_REVISION_LENGTH = 512
_LFS_MAX_BYTES = 1024
DEFAULT_GIT_TIMEOUT_SECONDS = 120.0

_KEPT_ENV = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "SYSTEMDRIVE",
    "COMSPEC",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
)


class GitAdapter:
    """Execute an allowlisted set of local Git reads against one work tree."""

    def __init__(
        self,
        repo: Path | str,
        *,
        git_executable: str = "git",
        timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
    ) -> None:
        self.repo = Path(repo).expanduser().resolve(strict=True)
        if not self.repo.is_dir():
            raise GitError(f"repository path is not a directory: {self.repo}")
        self.git_executable = git_executable
        self.timeout_seconds = timeout_seconds
        self.invocations: list[tuple[str, ...]] = []
        self._temp = tempfile.TemporaryDirectory(prefix="offline-review-")
        temp_root = Path(self._temp.name)
        self._empty_config = temp_root / "gitconfig"
        self._empty_config.write_bytes(b"")
        self._hooks = temp_root / "hooks"
        self._hooks.mkdir()
        self._cwd = temp_root / "cwd"
        self._cwd.mkdir()
        self._env = _sanitized_env(self._empty_config)
        self._closed = False

    def __enter__(self) -> GitAdapter:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._temp.cleanup()
            self._closed = True

    def ensure_work_tree(self) -> None:
        result = self._run(["rev-parse", "--is-inside-work-tree"], check=False)
        if result.returncode != 0 or result.stdout.strip() != b"true":
            raise GitError("path is not a Git work tree")

    def absolute_git_dir(self) -> Path:
        result = self._run(["rev-parse", "--absolute-git-dir"])
        text = result.stdout.decode("utf-8", "surrogateescape").strip()
        if not text:
            raise GitError("Git did not report an absolute git directory")
        return Path(text)

    def resolve_commit(self, revision: str) -> str:
        """Return the full commit object ID for ``revision``."""

        checked = validate_revision(revision)
        result = self._run(
            ["rev-parse", "--verify", "--end-of-options", f"{checked}^{{commit}}"],
            check=False,
        )
        if result.returncode != 0:
            raise RevisionError(f"cannot resolve revision to a commit: {_preview(checked)}")
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(lines) != 1:
            raise RevisionError(f"revision did not resolve to one commit: {_preview(checked)}")
        sha = lines[0].decode("ascii", "strict").lower()
        _validate_sha(sha)
        return sha

    def merge_base(self, base: str, head: str) -> str | None:
        """Return Git's single best merge base, or None when histories are unrelated."""

        return self._merge_base(base, head, all_bases=False)

    def merge_bases(self, base: str, head: str) -> tuple[str, ...]:
        found = self._merge_base(base, head, all_bases=True)
        if found is None:
            return ()
        if isinstance(found, str):
            return (found,)
        return found

    def diff_raw(self, left: str, right: str) -> tuple[bytes, str]:
        result = self._diff(left, right, "--raw", "-z", "--abbrev=64")
        return result.stdout, _stderr_text(result)

    def diff_patch(self, left: str, right: str) -> tuple[str, str]:
        result = self._diff(left, right, "--patch", "-U0", "--full-index")
        text = result.stdout.decode("utf-8", "surrogateescape")
        return text, _stderr_text(result)

    def worktree_differs_from_head(self) -> bool:
        """Report whether HEAD differs from the index, work tree, or untracked files.

        ``git diff HEAD`` and ``git ls-files`` are read-only. Uncommitted content
        is never used as review input.
        """

        diff = self._run(
            [
                "diff",
                "--raw",
                "-z",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--end-of-options",
                "HEAD",
            ],
            check=False,
        )
        if diff.returncode != 0:
            raise GitError("cannot compare the work tree with HEAD")
        untracked = self._run(
            ["ls-files", "--others", "--exclude-standard", "-z"],
            check=False,
        )
        if untracked.returncode != 0:
            raise GitError("cannot list untracked files")
        return bool(diff.stdout) or bool(untracked.stdout)

    def read_bytes(self, commit: str, path: str) -> bytes | None:
        """Read a blob from ``commit:path`` without touching the work tree."""

        _validate_sha(commit)
        if path == "" or "\n" in path or "\0" in path:
            raise GitError("refusing to read a path that contains a newline or NUL")
        blobs = self._cat_file_batch([f"{commit}:{path}"])
        return blobs[0]

    def read_blob_metadata(self, shas: list[str]) -> list[tuple[str, int] | None]:
        if not shas:
            return []
        for sha in shas:
            _validate_sha(sha)
        result = self._run(
            ["cat-file", "--batch-check"],
            input_bytes=b"".join(f"{sha}\n".encode("ascii") for sha in shas),
        )
        return _parse_batch_check(result.stdout, len(shas))

    def read_blobs(self, shas: list[str]) -> list[bytes | None]:
        for sha in shas:
            _validate_sha(sha)
        return self._cat_file_batch(shas)

    def list_tree(self, commit: str) -> tuple[tuple[str, str, str], ...]:
        """Return ``(mode, blob_sha, path)`` for every blob in ``commit``.

        ``git ls-tree`` reads the commit tree only. It does not recurse into
        submodules or check out files.
        """

        _validate_sha(commit)
        result = self._run(["ls-tree", "-r", "-z", commit])
        return parse_ls_tree_z(result.stdout)

    def _cat_file_batch(self, specs: list[str]) -> list[bytes | None]:
        if not specs:
            return []
        payload = b"".join(
            spec.encode("utf-8", "surrogateescape") + b"\n" for spec in specs
        )
        result = self._run(["cat-file", "--batch"], input_bytes=payload)
        return _parse_batch(result.stdout, len(specs))

    def _merge_base(
        self, base: str, head: str, *, all_bases: bool
    ) -> str | tuple[str, ...] | None:
        _validate_sha(base)
        _validate_sha(head)
        args = ["merge-base"]
        if all_bases:
            args.append("--all")
        args.extend(["--end-of-options", base, head])
        result = self._run(args, check=False)
        if result.returncode == 1 and not result.stdout.strip():
            return None
        if result.returncode != 0:
            raise GitError("git merge-base failed")
        lines = [line.strip().lower() for line in result.stdout.decode("ascii").splitlines()]
        shas = [line for line in lines if line]
        if not shas:
            return None
        normalized = tuple(self.resolve_commit(sha) for sha in shas)
        if all_bases:
            return normalized
        if len(normalized) != 1:
            raise GitError("git merge-base returned more than one commit")
        return normalized[0]

    def _diff(self, left: str, right: str, *format_args: str) -> subprocess.CompletedProcess[bytes]:
        _validate_sha(left)
        _validate_sha(right)
        return self._run(
            [
                "diff",
                *format_args,
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "-M",
                "-C",
                "--submodule=short",
                "--end-of-options",
                left,
                right,
            ]
        )

    def _run(
        self,
        args: list[str],
        *,
        check: bool = True,
        input_bytes: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        if self._closed:
            raise GitError("Git adapter is closed")
        if not args:
            raise GitError("refusing to run Git without a subcommand")
        subcommand = args[0]
        if subcommand in _BLOCKED_SUBCOMMANDS or subcommand not in _ALLOWED_SUBCOMMANDS:
            raise GitError(f"refusing Git subcommand: {subcommand}")
        command = [
            self.git_executable,
            "-C",
            str(self.repo),
            "--no-optional-locks",
            "-c",
            f"core.hooksPath={self._hooks}",
            "-c",
            "core.fsmonitor=",
            "-c",
            "core.useBuiltinFSMonitor=false",
            "-c",
            "diff.external=",
            "-c",
            "diff.renames=true",
            "-c",
            "diff.renameLimit=10000",
            "-c",
            "diff.mnemonicPrefix=false",
            "-c",
            "diff.noprefix=false",
            "-c",
            "diff.relative=false",
            "-c",
            "diff.submodule=short",
            "-c",
            "core.quotepath=false",
            "-c",
            "status.submoduleSummary=false",
            "-c",
            "fetch.recurseSubmodules=false",
            "-c",
            "submodule.recurse=false",
            "-c",
            "advice.detachedHead=false",
            "-c",
            "protocol.allow=never",
            "-c",
            "credential.helper=",
            *[
                item
                for name in sorted(_ALLOWED_SUBCOMMANDS)
                for item in ("-c", f"alias.{name}=")
            ],
            "-c",
            "extensions.worktreeConfig=false",
            *args,
        ]
        self.invocations.append(tuple(command))
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW
        try:
            completed = subprocess.run(
                command,
                cwd=self._cwd,
                shell=False,
                check=False,
                capture_output=True,
                timeout=self.timeout_seconds,
                env=self._env,
                input=input_bytes,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"Git command timed out: {subcommand}") from exc
        except FileNotFoundError as exc:
            raise GitError("git executable was not found") from exc
        if check and completed.returncode != 0:
            detail = _stderr_text(completed)
            message = f"Git command failed: {subcommand}"
            if detail:
                message = f"{message}: {detail[:500]}"
            raise GitError(message)
        return completed


def parse_ls_tree_z(data: bytes) -> tuple[tuple[str, str, str], ...]:
    """Parse ``git ls-tree -r -z`` records into mode, blob id, and path."""

    entries: list[tuple[str, str, str]] = []
    for record in data.split(b"\0"):
        if not record:
            continue
        tab = record.find(b"\t")
        if tab < 0:
            raise GitError("git ls-tree record is missing a path separator")
        meta = record[:tab].decode("ascii", "strict")
        parts = meta.split(" ")
        if len(parts) != 3:
            raise GitError("git ls-tree record does not have mode, type, and object")
        mode, object_type, sha = parts
        if object_type != "blob":
            continue
        sha = sha.lower()
        _validate_sha(sha)
        path = decode_git_path(record[tab + 1 :])
        if path == "" or "\n" in path or "\0" in path:
            raise GitError("git ls-tree returned a path the tool cannot address")
        entries.append((mode, sha, path))
    return tuple(entries)


def validate_revision(revision: str) -> str:
    if not isinstance(revision, str) or revision == "" or revision != revision.strip():
        raise RevisionError("revision must be a non-empty string without surrounding whitespace")
    if len(revision) > _MAX_REVISION_LENGTH:
        raise RevisionError("revision exceeds the supported length")
    if revision.startswith("-"):
        raise RevisionError("revision must not start with '-'")
    if _CONTROL.search(revision):
        raise RevisionError("revision contains a control character")
    lowered = revision.lower()
    if "://" in revision or "ext::" in lowered:
        raise RevisionError(
            "revision must be a local Git revision, not a URL or remote helper"
        )
    return revision


def is_lfs_pointer(data: bytes) -> bool:
    if len(data) > _LFS_MAX_BYTES or b"\0" in data:
        return False
    if not data.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
        return False
    has_oid = False
    has_size = False
    for line in data.splitlines():
        if line.startswith(b"oid sha256:") and len(line) >= len(b"oid sha256:") + 64:
            has_oid = True
        elif line.startswith(b"size ") and line[5:].isdigit():
            has_size = True
    return has_oid and has_size


def is_within(child: Path, parent: Path) -> bool:
    child_path = Path(os.path.normcase(str(child)))
    parent_path = Path(os.path.normcase(str(parent)))
    try:
        child_path.relative_to(parent_path)
    except ValueError:
        return False
    return True


def _validate_sha(sha: str) -> None:
    if not _SHA.fullmatch(sha):
        raise GitError("refusing to pass a non-object-id to Git")


def _sanitized_env(empty_config: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _KEPT_ENV:
        value = os.environ.get(key)
        if value:
            env[key] = value
    env["GIT_CONFIG_GLOBAL"] = str(empty_config)
    env["GIT_CONFIG_SYSTEM"] = str(empty_config)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_COUNT"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_EXTERNAL_DIFF"] = ""
    env["GCM_INTERACTIVE"] = "Never"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return env


def _stderr_text(result: subprocess.CompletedProcess[bytes]) -> str:
    text = result.stderr.decode("utf-8", "backslashreplace").strip()
    return redact_secrets(text)


def _preview(revision: str) -> str:
    if len(revision) <= 80:
        return repr(revision)
    return repr(revision[:80] + "...")


def _parse_batch(data: bytes, count: int) -> list[bytes | None]:
    results: list[bytes | None] = []
    cursor = 0
    for _ in range(count):
        newline = data.find(b"\n", cursor)
        if newline < 0:
            raise GitError("truncated git cat-file header")
        header = data[cursor:newline]
        cursor = newline + 1
        if header.endswith(b" missing"):
            results.append(None)
            continue
        metadata = _header_type_and_size(header)
        if metadata is None:
            raise GitError("unexpected git cat-file header")
        _object_type, size = metadata
        blob = data[cursor : cursor + size]
        if len(blob) != size:
            raise GitError("truncated git cat-file object")
        cursor += size
        if data[cursor : cursor + 1] != b"\n":
            raise GitError("git cat-file object is missing its trailing newline")
        cursor += 1
        results.append(blob)
    if cursor != len(data):
        raise GitError("trailing data after git cat-file output")
    return results


def _parse_batch_check(data: bytes, count: int) -> list[tuple[str, int] | None]:
    lines = data.splitlines()
    if len(lines) != count:
        raise GitError("git cat-file --batch-check returned an unexpected record count")
    results: list[tuple[str, int] | None] = []
    for line in lines:
        if line.endswith(b" missing"):
            results.append(None)
            continue
        metadata = _header_type_and_size(line)
        if metadata is None:
            raise GitError("unexpected git cat-file --batch-check header")
        results.append(metadata)
    return results


def _header_type_and_size(header: bytes) -> tuple[str, int] | None:
    match = re.search(rb" (blob|tree|commit|tag) (\d+)$", header)
    if match is None:
        return None
    return match.group(1).decode("ascii"), int(match.group(2))
