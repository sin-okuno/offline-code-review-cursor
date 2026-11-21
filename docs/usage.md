# Offline review CLI usage

This tool is a local, read-only context extractor. It resolves the requested revisions to commit object IDs, diffs those commits, and writes changed paths, old and new line ranges, affected Python definitions, and file-level import candidates. Angular template and component analysis stays out of scope and is labeled in the report.

## Command

```text
python -m offline_review analyze --repo PATH --base REF --head REF --output DIR --comparison merge-base|direct [--python-root REL ...] [--cache DIR]
```

PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m offline_review analyze --repo PATH --base REF --head REF --output DIR --comparison merge-base --python-root src --cache DIR
```

`--repo`, `--base`, `--head`, `--output`, and `--comparison` are required. `--python-root` and `--cache` are optional. `--python-root` may be repeated. There is no hidden comparison mode, output location, import root, or cache directory.

The process exit codes are:

| Exit | Meaning |
| --- | --- |
| 0 | Both reports were written. Warnings may still say coverage is partial. |
| 1 | A revision, Git read, or diff parse failed. No report is written. |
| 2 | The invocation is invalid, including an output directory inside the repository. |

`--output` must be outside the work tree and outside `.git`. This repository ignores `output/`.

The reports are `review-context.json` and `review-context.md`. The JSON schema version is `1.1.0`. Object keys are sorted so the same commits produce the same bytes. The phase field is `2-python`. Schema 1.1.0 adds affected Python symbols and file-level import relationships to the 1.0.0 diff report.

## Comparison modes

Both modes first resolve `--base` and `--head` with `git rev-parse --verify <rev>^{commit}`. Later diff commands receive those full object IDs, not the original ref text.

`merge-base` is three-dot comparison. The diff base is the commit returned by `git merge-base <base> <head>`, and the diff is that commit to head. This matches `git diff <base>...<head>`: changes reachable from head since the histories diverged. If Git finds several merge bases, the report lists them and diffs the single commit `git merge-base` selects. Unrelated histories have no merge base, and this mode stops with an error.

`direct` is two-dot comparison. The diff is from the resolved base commit to the resolved head commit, matching `git diff <base> <head>`. The merge base is still recorded when it exists. It is not the diff base. Unrelated histories produce a report with a `NO_MERGE_BASE` warning.

On a linear history the two modes select the same diff base, because the base commit is the merge base.

## What the report contains

Each changed file records status, similarity for renames and copies, old and new paths, modes, blob IDs, the diff-base commit, the head commit, and hunks. A hunk records its header as evidence and the old and new changed line numbers. Deletion-only hunks have a new count of zero and no new line numbers. Addition-only hunks have an old count of zero.

The same Git rename and copy detection as `git diff -M -C` is enabled, with `diff.renameLimit=10000`. `-C` finds copies whose source was modified in the same diff. It does not enable `--find-copies-harder`.

These cases are reported instead of being treated as normal source text:

- Binary files have no line ranges (`BINARY_FILE`).
- Mode `160000` is a submodule gitlink. The linked repository is not fetched or traversed (`SUBMODULE`).
- Mode `120000` is a symlink. The target is not followed (`SYMLINK`).
- A small blob with the Git LFS pointer header is reported as a pointer. Smudge and download are not run (`LFS_POINTER`).
- Type changes use status `T` (`TYPE_CHANGED`).
- `.ts`, `.tsx`, `.html`, `.scss`, `.less`, and `.css` paths keep their diffs only (`ANGULAR_SEMANTIC_OUT_OF_SCOPE`).

## Python symbols and imports

`--python-root` is repeatable and optional. Each value is a repository-relative directory that acts as an import root. `.` means the repository root. There is no hidden root: if the option is omitted, Python files are still parsed, but module names are not assigned and import targets are not matched (`PYTHON_ROOTS_NOT_CONFIGURED`).

For every changed `.py` path, the tool reads the blob from the diff-base commit and from the head commit. Changed lines on each side map to the narrowest class, function, async function, or top-level statement, plus the enclosing class and function chain. A deletion-only hunk has old lines and no new lines, so only the old definition is marked affected. Nested functions keep a dotted qualname such as `Outer.method.inner`. Duplicate qualnames gain a `@line` suffix.

Syntax errors, undecodable bytes, NUL bytes, and files larger than 1 MiB produce a warning and no symbols for that blob. The line ranges from the diff remain.

The import index lists every tracked regular `.py` blob in the diff base and in head, parsing each blob once. It records outgoing imports of changed files and incoming imports of their module names, including imports from files that did not change. Certainty is:

| Certainty | Meaning |
| --- | --- |
| confirmed | A direct or parent-module import names exactly one file whose module name is unambiguous. |
| uncertain | The path matches more than one root, more than one file owns the module name, the name may be a submodule or an attribute (`from module import name`), or the call is a literal `import_module` / `__import__`. |
| unresolved | No indexed file has that module name, or a relative import escapes the package. Standard-library and third-party imports are unresolved unless those files are inside a configured root. |

Non-literal dynamic imports are not given a target (`PYTHON_DYNAMIC_IMPORT`). Attribute lookups, Django relations, and name binding inside functions are not resolved. Overlapping roots stay as separate candidate module names instead of silently choosing the longest root.

`--cache` is an optional directory outside the repository. It stores each commit's parsed symbols and import statements, keyed by the commit id, the parser version, and the exact `--python-root` list. A second run with the same key does not list or parse that commit again. Changing the commit, the parser version, or the roots misses. The directory is capped at 64 MiB by deleting the oldest records first. A single record larger than the cap is kept. The cache holds syntax excerpts and import text, so treat it as confidential. Omit `--cache` to parse on every run. The report bytes do not depend on whether the cache was used.

The import index is still one walk per commit. Changed files read the forward and reverse maps. They do not scan the tree once per changed file. Source bytes are read in chunks of 32 blobs and dropped after parsing.

`analysis_complete` is true only when the report has no partial-coverage reason. Angular files, binaries, submodules, symlinks, LFS pointers, missing roots, unresolved imports, and parse failures keep it false.

A dirty index, a work tree that differs from HEAD, or untracked files add `WORKTREE_DIRTY`. The diff still uses the resolved commits. Uncommitted file contents are not read and are not copied into the report.

## Git safety and residual risk

The CLI allowlists `rev-parse`, `merge-base`, `diff`, `cat-file`, `ls-files`, and `ls-tree`. It refuses `clone`, `fetch`, `pull`, `push`, `remote`, `ls-remote`, `submodule`, and `upload-pack`. Diffs pass `--no-ext-diff` and `--no-textconv`. The Git process is started with `shell=False` and a sanitized environment that drops credential variables such as `GIT_ASKPASS`. Each command also sets `protocol.allow=never`, an empty `credential.helper`, empty aliases for every allowlisted subcommand, `core.fsmonitor` cleared, `GIT_NO_LAZY_FETCH=1`, `GIT_LFS_SKIP_SMUDGE=1`, an empty global and system config, and `core.hooksPath` pointed at an empty temporary directory. Revisions that start with `-`, contain control characters, contain `://`, or contain `ext::` are rejected before Git sees them, and the rejected text is not copied into the error. Blob reads use `git cat-file`, which does not apply smudge filters. Git stderr, syntax-error excerpts, and import evidence are passed through a redactor that removes URL userinfo, bearer tokens, private keys, and `password=` / `secret=` / token assignments.

The output directory is resolved before writing. A path whose real location is inside the work tree or the git directory is rejected. A report file that is already a symlink or junction is also rejected, and each report is written to a new temporary file in that directory and then renamed over the destination. The report stores paths, blob ids, hunk headers, changed line numbers, syntax excerpts, and the resolved repository path. It does not store full file bodies. Treat the directory as confidential anyway. Delete reports when they are no longer needed; the tool does not upload them or apply a retention period.

Residual risk remains:

- The `git` executable on `PATH` is trusted. A replaced Git binary can ignore these flags.
- Repository-local configuration is overridden for hooks, external diff, textconv, fsmonitor, credential helpers, allowlisted aliases, protocol, and rename limits on the commands this tool runs. Other Git tools used outside this CLI are unchanged. Built-in Git commands are not replaced by aliases; the empty alias settings are defense in depth.
- `protocol.allow=never` blocks Git transports for these commands. It is not an operating-system firewall. Network enforcement on Windows or WSL is a separate control. `HOME` is still passed so Git can start; a trusted Git could read a credential store if a command asked it to. These commands do not.
- A symlink created in the output directory after the reparse check and before the rename can still win a race. The tool does not hold a lock across that window.
- Company remote authentication, including HTTPS client certificates, stays with the approved Git client that fetched the repository before review. This tool never configures certificates or keys.
- Without `--cache`, indexing reads every regular `.py` blob in both selected commits once. That cost follows the size of the Python trees, not the number of changed files times the size of the tree. `--cache` keeps that parse and a hit skips the tree list and parse for the same commit, parser version, and import roots. The per-file parse cap is 1 MiB. A synthetic run of 100005 lines is recorded in `docs/validation.md`. A cache hit on that run was faster and used more peak memory, because both commit records are decoded at once.

Reviewed project scripts, hooks, tests, and package managers are not executed. File contents come from Git objects for the selected commits.

## Tests

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

The tests build temporary Git repositories. They do not read a company repository.

## Troubleshooting

`git executable was not found` means `git` is not on `PATH`.

`cannot resolve revision to a commit` means the ref is missing or does not peel to a commit. The original text is not passed to `git diff`.

`revision must be a local Git revision, not a URL or remote helper` means the argument contained `://` or `ext::`. The tool does not print that argument back.

`refusing to write through a symlink` means a report path in the output directory is a reparse point. Remove it and choose a normal directory outside the repository.

`no merge base exists` means `--comparison merge-base` cannot choose a three-dot base. `--comparison direct` can still diff the two commits.

`output directory must be outside the repository` or `parse cache directory must be outside the repository` means that path would create files in the work tree. Choose a directory beside the repository.

`The work tree or index differs from HEAD` is a warning, not a failure. Commit or discard the local edits before review if the committed range is not the content you intend to inspect; the tool will not inspect those edits for you.
