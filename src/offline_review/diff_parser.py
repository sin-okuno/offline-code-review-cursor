"""Parse read-only Git raw and unified diffs into old and new line ranges."""

from __future__ import annotations

import re

from offline_review.models import DiffParseError, Hunk, LineRange

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*$")
_ZERO_OBJECT = re.compile(r"^0+$")
_META_PREFIXES = (
    "diff --git ",
    "index ",
    "old mode ",
    "new mode ",
    "new file mode ",
    "deleted file mode ",
    "similarity index ",
    "dissimilarity index ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
    "--- ",
    "+++ ",
    "Binary files ",
    "GIT binary patch",
)


class ParsedFile:
    """One changed path before commit SHAs and LFS flags are attached."""

    __slots__ = (
        "binary",
        "hunks",
        "new_blob",
        "new_mode",
        "new_path",
        "old_blob",
        "old_mode",
        "old_path",
        "similarity",
        "status_code",
        "submodule",
        "symlink",
    )

    def __init__(
        self,
        *,
        status_code: str,
        similarity: int | None,
        old_path: str | None,
        new_path: str | None,
        old_mode: str,
        new_mode: str,
        old_blob: str | None,
        new_blob: str | None,
        binary: bool,
        submodule: bool,
        symlink: bool,
        hunks: tuple[Hunk, ...],
    ) -> None:
        self.status_code = status_code
        self.similarity = similarity
        self.old_path = old_path
        self.new_path = new_path
        self.old_mode = old_mode
        self.new_mode = new_mode
        self.old_blob = old_blob
        self.new_blob = new_blob
        self.binary = binary
        self.submodule = submodule
        self.symlink = symlink
        self.hunks = hunks


def _json_safe(text: str) -> str:
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")


def decode_git_path(data: bytes) -> str:
    """Decode a Git path byte string into a JSON-safe Unicode string.

    Valid UTF-8 is preserved. Undecodable bytes become ``\\xHH`` escapes so
    later JSON encoding cannot fail and cannot be mistaken for a different path.
    """

    return data.decode("utf-8", "surrogateescape").encode(
        "utf-8", "surrogateescape"
    ).decode("utf-8", "backslashreplace")


def coalesce_lines(lines: tuple[int, ...]) -> tuple[LineRange, ...]:
    if not lines:
        return ()
    ordered = tuple(sorted(set(lines)))
    ranges: list[LineRange] = []
    start = previous = ordered[0]
    for number in ordered[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(LineRange(start, previous))
        start = previous = number
    ranges.append(LineRange(start, previous))
    return tuple(ranges)


def parse_raw_diff_z(data: bytes) -> tuple[ParsedFile, ...]:
    """Parse ``git diff --raw -z`` output. Paths are taken only from NUL records."""

    files: list[ParsedFile] = []
    cursor = 0
    length = len(data)
    while cursor < length:
        if data[cursor : cursor + 1] != b":":
            raise DiffParseError("raw diff record does not start with ':'")
        header_end = data.find(b"\0", cursor)
        if header_end < 0:
            raise DiffParseError("raw diff header is missing a NUL terminator")
        header = data[cursor + 1 : header_end].decode("ascii", "strict")
        parts = header.split(" ")
        if len(parts) != 5:
            raise DiffParseError(f"raw diff header has {len(parts)} fields")
        old_mode, new_mode, old_sha, new_sha, status = parts
        old_sha = old_sha.lower()
        new_sha = new_sha.lower()
        cursor = header_end + 1
        first_path, cursor = _read_z_path(data, cursor)
        status_code, similarity = _split_status(status)
        second_path: str | None = None
        if status_code in {"R", "C"}:
            second_path, cursor = _read_z_path(data, cursor)
            old_path: str | None = first_path
            new_path: str | None = second_path
        elif status_code == "A":
            old_path = None
            new_path = first_path
        elif status_code == "D":
            old_path = first_path
            new_path = None
        else:
            old_path = first_path
            new_path = first_path
        files.append(
            ParsedFile(
                status_code=status_code,
                similarity=similarity,
                old_path=old_path,
                new_path=new_path,
                old_mode=old_mode,
                new_mode=new_mode,
                old_blob=_blob_or_none(old_sha),
                new_blob=_blob_or_none(new_sha),
                binary=False,
                submodule=old_mode == "160000" or new_mode == "160000",
                symlink=old_mode == "120000" or new_mode == "120000",
                hunks=(),
            )
        )
    return tuple(files)


def parse_unified_diff(text: str) -> tuple[_PatchSection, ...]:
    if text.strip() == "":
        return ()
    lines = text.splitlines()
    if not lines[0].startswith("diff --git "):
        raise DiffParseError("unified diff does not start with 'diff --git'")
    sections: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("diff --git ") and current:
            sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)
    return tuple(_parse_section(section) for section in sections)


def combine_diff(raw_entries: tuple[ParsedFile, ...], patch_text: str) -> tuple[ParsedFile, ...]:
    """Attach unified hunks to raw entries in Git's emission order.

    Raw ``-z`` records own the path and status. The patch is accepted only when
    it has the same number of files and, where a path header exists, the same
    paths. A mismatch raises instead of pairing a hunk with the wrong file.
    """

    sections = parse_unified_diff(patch_text)
    if len(sections) != len(raw_entries):
        raise DiffParseError(
            f"raw diff has {len(raw_entries)} files but the unified diff has {len(sections)}"
        )
    combined: list[ParsedFile] = []
    for entry, section in zip(raw_entries, sections, strict=True):
        if section.hunks and not section.paths_reliable:
            raise DiffParseError("unified hunks have no parseable path header")
        if section.paths_reliable and (
            section.old_path != entry.old_path or section.new_path != entry.new_path
        ):
            raise DiffParseError(
                "unified diff path does not match the raw diff path: "
                f"raw {entry.old_path!r} -> {entry.new_path!r}, "
                f"patch {section.old_path!r} -> {section.new_path!r}"
            )
        if section.binary and section.hunks:
            raise DiffParseError("binary diff also contains text hunks")
        combined.append(
            ParsedFile(
                status_code=entry.status_code,
                similarity=entry.similarity,
                old_path=entry.old_path,
                new_path=entry.new_path,
                old_mode=entry.old_mode,
                new_mode=entry.new_mode,
                old_blob=entry.old_blob,
                new_blob=entry.new_blob,
                binary=section.binary or entry.binary,
                submodule=entry.submodule,
                symlink=entry.symlink,
                hunks=() if entry.submodule else section.hunks,
            )
        )
    return tuple(combined)


class _PatchSection:
    __slots__ = ("binary", "hunks", "new_path", "old_path", "paths_reliable")

    def __init__(
        self,
        *,
        old_path: str | None,
        new_path: str | None,
        paths_reliable: bool,
        binary: bool,
        hunks: tuple[Hunk, ...],
    ) -> None:
        self.old_path = old_path
        self.new_path = new_path
        self.paths_reliable = paths_reliable
        self.binary = binary
        self.hunks = hunks


def _parse_section(lines: list[str]) -> _PatchSection:
    old_path: str | None = None
    new_path: str | None = None
    saw_rename = False
    saw_prefixed = False
    binary = False
    hunks: list[Hunk] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("GIT binary patch") or line.startswith("Binary files "):
            binary = True
            break
        header = _HUNK_HEADER.match(line)
        if header:
            hunk, index = _parse_hunk(lines, index, header)
            hunks.append(hunk)
            continue
        if line.startswith("rename from ") or line.startswith("copy from "):
            old_path = _decode_token(line.split(" ", 2)[2])
            saw_rename = True
        elif line.startswith("rename to ") or line.startswith("copy to "):
            new_path = _decode_token(line.split(" ", 2)[2])
            saw_rename = True
        elif line.startswith("--- "):
            parsed = _prefixed_path(line[4:])
            if not saw_rename:
                old_path = parsed
                saw_prefixed = True
        elif line.startswith("+++ "):
            parsed = _prefixed_path(line[4:])
            if not saw_rename:
                new_path = parsed
                saw_prefixed = True
        elif not any(line.startswith(prefix) for prefix in _META_PREFIXES):
            raise DiffParseError(f"unexpected diff metadata line: {line[:80]!r}")
        index += 1
    return _PatchSection(
        old_path=old_path,
        new_path=new_path,
        paths_reliable=saw_rename or saw_prefixed,
        binary=binary,
        hunks=tuple(hunks),
    )


def _parse_hunk(
    lines: list[str], index: int, header: re.Match[str]
) -> tuple[Hunk, int]:
    old_start = int(header.group(1))
    old_count = int(header.group(2)) if header.group(2) is not None else 1
    new_start = int(header.group(3))
    new_count = int(header.group(4)) if header.group(4) is not None else 1
    evidence = _json_safe(lines[index])
    index += 1
    old_lines: list[int] = []
    new_lines: list[int] = []
    old_cursor = old_start
    new_cursor = new_start
    consumed_old = 0
    consumed_new = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("@@"):
            break
        if line.startswith("\\"):
            index += 1
            continue
        if line.startswith("+"):
            _record_line(new_lines, new_cursor)
            new_cursor += 1
            consumed_new += 1
        elif line.startswith("-"):
            _record_line(old_lines, old_cursor)
            old_cursor += 1
            consumed_old += 1
        elif line.startswith(" "):
            old_cursor += 1
            new_cursor += 1
            consumed_old += 1
            consumed_new += 1
        else:
            raise DiffParseError(f"unexpected hunk line: {line[:80]!r}")
        index += 1
    if consumed_old != old_count or consumed_new != new_count:
        raise DiffParseError(
            "hunk length does not match its header: "
            f"old {consumed_old}/{old_count}, new {consumed_new}/{new_count}"
        )
    if old_count and not new_count:
        kind = "deletion"
    elif new_count and not old_count:
        kind = "addition"
    else:
        kind = "edit"
    old_tuple = tuple(old_lines)
    new_tuple = tuple(new_lines)
    hunk = Hunk(
        kind=kind,
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        old_lines=old_tuple,
        new_lines=new_tuple,
        old_line_ranges=coalesce_lines(old_tuple),
        new_line_ranges=coalesce_lines(new_tuple),
        evidence=evidence,
    )
    return hunk, index


def _record_line(sink: list[int], number: int) -> None:
    if number < 1:
        raise DiffParseError(f"diff produced a non-positive line number: {number}")
    sink.append(number)


def _read_z_path(data: bytes, cursor: int) -> tuple[str, int]:
    end = data.find(b"\0", cursor)
    if end < 0:
        raise DiffParseError("raw diff path is missing a NUL terminator")
    return decode_git_path(data[cursor:end]), end + 1


def _split_status(status: str) -> tuple[str, int | None]:
    if not status:
        raise DiffParseError("raw diff status is empty")
    code = status[0]
    if code not in {"A", "C", "D", "M", "R", "T", "U", "X"}:
        raise DiffParseError(f"unsupported raw diff status {status!r}")
    if code in {"R", "C"}:
        if len(status) == 1:
            return code, None
        score = status[1:]
        if not score.isdigit():
            raise DiffParseError(f"invalid rename or copy score {status!r}")
        return code, int(score)
    if len(status) != 1:
        raise DiffParseError(f"invalid raw diff status {status!r}")
    return code, None


def _blob_or_none(sha: str) -> str | None:
    if _ZERO_OBJECT.fullmatch(sha):
        return None
    return sha


def _decode_token(token: str) -> str:
    if token.startswith('"'):
        return _unquote_git_path(token)
    return token


def _prefixed_path(rest: str) -> str | None:
    token = _path_token_from_header(rest)
    path = _decode_token(token)
    if path == "/dev/null":
        return None
    if len(path) >= 2 and path[0] in {"a", "b"} and path[1] == "/":
        return path[2:]
    return path


def _path_token_from_header(rest: str) -> str:
    if rest.startswith('"'):
        end = _closing_quote(rest)
        return rest[: end + 1]
    tab = rest.find("\t")
    if tab >= 0:
        return rest[:tab]
    return rest


def _closing_quote(token: str) -> int:
    escaped = False
    for index in range(1, len(token)):
        character = token[index]
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == '"':
            return index
    raise DiffParseError("unterminated quoted diff path")


def _unquote_git_path(token: str) -> str:
    if len(token) < 2 or not token.endswith('"'):
        raise DiffParseError("quoted diff path is incomplete")
    body = token[1:-1].encode("ascii", "strict")
    output = bytearray()
    index = 0
    while index < len(body):
        current = body[index]
        if current != 0x5C:
            output.append(current)
            index += 1
            continue
        index += 1
        if index >= len(body):
            raise DiffParseError("quoted diff path ends with a backslash")
        escaped = body[index]
        simple = {
            0x5C: 0x5C,
            0x22: 0x22,
            ord("n"): 0x0A,
            ord("t"): 0x09,
            ord("r"): 0x0D,
            ord("a"): 0x07,
            ord("b"): 0x08,
            ord("f"): 0x0C,
            ord("v"): 0x0B,
        }
        if escaped in simple:
            output.append(simple[escaped])
            index += 1
            continue
        if 0x30 <= escaped <= 0x37:
            octal = bytes([escaped])
            index += 1
            for _ in range(2):
                if index < len(body) and 0x30 <= body[index] <= 0x37:
                    octal += bytes([body[index]])
                    index += 1
                else:
                    break
            output.append(int(octal, 8))
            continue
        raise DiffParseError("unsupported escape in a quoted diff path")
    return decode_git_path(bytes(output))
