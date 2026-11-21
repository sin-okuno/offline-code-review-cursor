"""Unit tests for raw and unified diff parsing."""

from __future__ import annotations

import unittest

from offline_review.diff_parser import (
    coalesce_lines,
    combine_diff,
    decode_git_path,
    parse_raw_diff_z,
    parse_unified_diff,
)
from offline_review.models import DiffParseError, LineRange


OLD = "a" * 40
NEW = "b" * 40
DELETED = "c" * 40
RENAMED_OLD = "d" * 40
RENAMED_NEW = "e" * 40


class DiffParserTests(unittest.TestCase):
    def test_coalesce_lines_merges_neighbors_and_keeps_gaps(self) -> None:
        self.assertEqual(
            coalesce_lines((4, 2, 3, 6, 2)),
            (LineRange(2, 4), LineRange(6, 6)),
        )
        self.assertEqual(coalesce_lines(()), ())

    def test_raw_diff_preserves_add_delete_rename_and_unicode_paths(self) -> None:
        raw = (
            f":100644 100644 {OLD} {NEW} M\0pkg/app.py\0"
            f":100644 000000 {DELETED} {'0' * 40} D\0pkg/gone.py\0"
            f":000000 100644 {'0' * 40} {NEW} A\0pkg/new.py\0"
            f":100644 100644 {RENAMED_OLD} {RENAMED_NEW} R100\0"
            "dir with space/ü.txt\0dir with space/ü renamed.txt\0"
            f":100644 100644 {OLD} {NEW} T\0link.txt\0"
            f":100644 160000 {OLD} {NEW} M\0vendor/lib\0"
        ).encode()
        files = parse_raw_diff_z(raw)
        self.assertEqual([item.status_code for item in files], ["M", "D", "A", "R", "T", "M"])
        self.assertIsNone(files[1].new_path)
        self.assertIsNone(files[1].new_blob)
        self.assertIsNone(files[2].old_path)
        self.assertEqual(files[3].old_path, "dir with space/ü.txt")
        self.assertEqual(files[3].new_path, "dir with space/ü renamed.txt")
        self.assertEqual(files[3].similarity, 100)
        self.assertTrue(files[4].symlink is False)
        self.assertTrue(files[5].submodule)
        self.assertEqual(decode_git_path("dir/ü".encode()), "dir/ü")

    def test_symlink_mode_is_detected(self) -> None:
        raw = f":100644 120000 {OLD} {NEW} T\0link.txt\0".encode()
        files = parse_raw_diff_z(raw)
        self.assertTrue(files[0].symlink)
        self.assertEqual(files[0].status_code, "T")

    def test_modified_and_deleted_hunks_keep_both_sides(self) -> None:
        patch = "\n".join(
            [
                "diff --git a/pkg/app.py b/pkg/app.py",
                f"index {OLD}..{NEW} 100644",
                "--- a/pkg/app.py",
                "+++ b/pkg/app.py",
                "@@ -2 +2 @@ alpha",
                "-bravo",
                "+bravo-changed",
                "@@ -4 +3,0 @@ charlie",
                "-delta",
                "diff --git a/pkg/gone.py b/pkg/gone.py",
                "deleted file mode 100644",
                f"index {DELETED}..{'0' * 40}",
                "--- a/pkg/gone.py",
                "+++ /dev/null",
                "@@ -1,3 +0,0 @@",
                "-one",
                "-two",
                "-three",
                "\\ No newline at end of file",
            ]
        )
        sections = parse_unified_diff(patch)
        self.assertEqual(sections[0].hunks[0].kind, "edit")
        self.assertEqual(sections[0].hunks[0].old_lines, (2,))
        self.assertEqual(sections[0].hunks[0].new_lines, (2,))
        self.assertEqual(sections[0].hunks[1].kind, "deletion")
        self.assertEqual(sections[0].hunks[1].old_lines, (4,))
        self.assertEqual(sections[0].hunks[1].new_lines, ())
        self.assertEqual(sections[0].hunks[1].new_count, 0)
        self.assertEqual(sections[1].hunks[0].kind, "deletion")
        self.assertEqual(sections[1].hunks[0].old_lines, (1, 2, 3))
        self.assertEqual(sections[1].hunks[0].new_lines, ())
        self.assertIsNone(sections[1].new_path)

    def test_quoted_rename_path_decodes_octal_utf8(self) -> None:
        patch = "\n".join(
            [
                'diff --git "a/dir/\\303\\274.txt" "b/dir/\\303\\274 renamed.txt"',
                "similarity index 100%",
                'rename from "dir/\\303\\274.txt"',
                'rename to "dir/\\303\\274 renamed.txt"',
            ]
        )
        section = parse_unified_diff(patch)[0]
        self.assertEqual(section.old_path, "dir/ü.txt")
        self.assertEqual(section.new_path, "dir/ü renamed.txt")
        self.assertEqual(section.hunks, ())

    def test_addition_binary_and_path_mismatch(self) -> None:
        addition = "\n".join(
            [
                "diff --git a/pkg/new.py b/pkg/new.py",
                "new file mode 100644",
                "--- /dev/null",
                "+++ b/pkg/new.py",
                "@@ -0,0 +1 @@",
                "+added",
            ]
        )
        section = parse_unified_diff(addition)[0]
        self.assertIsNone(section.old_path)
        self.assertEqual(section.hunks[0].kind, "addition")
        self.assertEqual(section.hunks[0].new_lines, (1,))
        binary = "\n".join(
            [
                "diff --git a/logo.bin b/logo.bin",
                "index 1111111..2222222 100644",
                "Binary files a/logo.bin and b/logo.bin differ",
            ]
        )
        self.assertTrue(parse_unified_diff(binary)[0].binary)
        raw = parse_raw_diff_z(f":100644 100644 {OLD} {NEW} M\0pkg/app.py\0".encode())
        with self.assertRaises(DiffParseError):
            combine_diff(raw, addition)

    def test_space_path_uses_tab_terminator(self) -> None:
        patch = "\n".join(
            [
                "diff --git a/old name.py b/old name.py",
                f"index {OLD}..{NEW} 100644",
                "--- a/old name.py\t",
                "+++ b/old name.py\t",
                "@@ -2 +1,0 @@ keep",
                "-delete-me",
            ]
        )
        section = parse_unified_diff(patch)[0]
        self.assertEqual(section.old_path, "old name.py")
        self.assertEqual(section.hunks[0].kind, "deletion")

    def test_malformed_hunk_and_truncated_raw_fail(self) -> None:
        with self.assertRaises(DiffParseError):
            parse_unified_diff(
                "\n".join(
                    [
                        "diff --git a/a.py b/a.py",
                        "--- a/a.py",
                        "+++ b/a.py",
                        "@@ -1 +1 @@",
                        "-old",
                    ]
                )
            )
        with self.assertRaises(DiffParseError):
            parse_raw_diff_z(b":100644 100644 incomplete")

    def test_empty_patch_and_empty_raw(self) -> None:
        self.assertEqual(parse_unified_diff(""), ())
        self.assertEqual(parse_raw_diff_z(b""), ())
        self.assertEqual(combine_diff((), ""), ())


if __name__ == "__main__":
    unittest.main()
