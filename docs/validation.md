# Validation

These results come from synthetic Git repositories created in temporary directories. No company repository, private remote URL, client certificate, private key, or production credential was read or sent anywhere.

Observed environment: Python 3.13.12, `git version 2.44.0.windows.1`, Windows. Current package version `0.4.0`. Report schema `1.1.0`. Phase `2-python`. The current suite is the 43-test run below. The 38-test hardening run and the one-file CLI smoke were observed on package `0.3.0` before the cache existed. WSL2 was already installed on this machine. Ubuntu 26.04 LTS was added beside the existing `docker-desktop` distro, and the same 43-test suite was run there on package `0.4.0`.

## Commands and observed results

Current full suite, package `0.4.0`, run from the workspace root:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

Observed on Windows: `Ran 43 tests in 79.995s` and `OK`.

The same command inside `wsl -d Ubuntu-26.04`, with `python` resolving to CPython 3.13.12 and Git 2.53.0, observed `Ran 43 tests in 4.161s` and `OK`. `wsl` without `-d` still starts `docker-desktop`. Ubuntu's own `python3` is 3.14.4 and is not the interpreter used for this suite.

Hardening suite on package `0.3.0`, before the cache tests existed: `Ran 38 tests in 59.663s` and `OK`.

CLI module smoke on package `0.3.0`, a synthetic one-file repository (`app.py`, `def ping` changed from `return 1` to `return 2`, `--python-root .`, `--comparison direct`):

```powershell
$env:PYTHONPATH = "src"
python -m offline_review analyze --repo <temp> --base <base-sha> --head HEAD --output <temp-outside> --comparison direct --python-root .
```

Observed: exit 0, stdout 0 bytes, stderr 0 bytes, wall clock 1.701 seconds. The JSON report had schema `1.1.0`, phase `2-python`, tool version `0.3.0`, `files_total` 1, `python_files_indexed` 2 (the same path in both commits), Python AST `parsed`, dependency index `complete`, and new symbol `ping`.

Scale check, also synthetic and separate from the unit suite: 200 modules `pkg/m000.py` … `pkg/m199.py` plus `pkg/app.py` (201 paths). Only `app.py` changed. The same CLI command finished in 2.118 seconds with exit 0 and stderr 0 bytes. Coverage recorded `files_total` 1, `python_files_indexed` 402, `python_files_parsed` 402, `python_files_failed` 0, `imports_confirmed` 2. Two confirmed edges named the unchanged file `pkg/m000.py`. Those 402 entries are one path per commit (201 paths in each of the two commits). Peak RSS was not measured for this 201-path run, and it did not print a separate count of blob-parse calls. The 100005-line measurement, including wall clock and peak RSS, is in the Performance section.

The Angular + Django REST acceptance fixture is `tests/test_hardening.py`. It is a temporary repository on branch `develop` with `frontend/` (component TypeScript, HTML, SCSS, `package.json`, `angular.json`) and `backend/` (settings, models, serializers, views, urls, a migration, 20 generated modules, `manage.py`). The head commit also has a syntax error, a Python file larger than 1 MiB, a rename, a deletion, a binary change, an LFS pointer change, a submodule gitlink change, and paths `notes/ü.txt` and `notes/release notes.txt`. `manage.py` would write a marker if executed. Before the CLI run, the test proved that `core.fsmonitor` and `diff.external` execute that marker, then deleted the marker. The CLI run left the marker absent, left both index files unchanged, and left `manage.py` unchanged.

## Audit

| Check | Observed result |
| --- | --- |
| Option injection via refs | Revisions that start with `-`, contain a control character, contain `://`, or contain `ext::` are rejected before Git runs. A URL carrying `s3cret-token` is not copied into the error text. Diffs receive resolved object ids after `--end-of-options`. |
| Shell execution | Git is started with `shell=False`. A fake Git executable received `rev-parse` as its own argument. |
| External diff and textconv | With the protective flags absent, a repository `diff.external` and an attribute `diff.*.command` wrote a marker. The adapter command includes `--no-ext-diff` and `--no-textconv`, and the marker stayed absent while the text diff was still parsed. |
| Credential leakage | `GITHUB_TOKEN`, `GIT_ASKPASS`, and `AWS_SECRET_ACCESS_KEY` set in the parent process were absent from the Git child environment. Git stderr containing a URL userinfo was stored as `https://***@example.invalid/synthetic.git`. A fixture remote URL with the same token did not appear in the JSON or Markdown. `SECRET_KEY` text from an unchanged settings file was not copied into the report. |
| Network-capable commands | `fetch` and `clone` are refused by the allowlist. Commands set `protocol.allow=never`. The smoke and the suite did not invoke a remote. |
| Git LFS | Pointer text is labeled `LFS_POINTER`. The blob is not smudged. A configured `filter.lfs.process` marker was not written. |
| Submodules | The gitlink commit ids are reported and the submodule tree is not walked. `submodule` is not an allowlisted command. |
| Binary diffs | `assets/logo.bin` is `binary` and has no hunks. |
| Unicode and space names | `notes/ü.txt` and `notes/release notes.txt` are present in the report. |
| Added, deleted, renamed | `backend/orders/health.py` is added, `backend/orders/legacy.py` is deleted with old symbol `retired` and no new lines, and `backend/orders/pricing.py` is renamed to `backend/orders/totals.py`. |
| Work tree differs from HEAD | After a committed run, an uncommitted serializer line and an untracked file produced `WORKTREE_DIRTY`. Those strings are absent from the report, and the affected symbols match the committed blob. |
| Malformed Python | `broken.py` is `syntax_error` with no symbols. `huge.py` is `too_large` and is not parsed. A syntax line containing a URL userinfo loses that userinfo in the warning. |
| Large-repository cost | Each selected commit is listed once and each Python blob is parsed once per object id. `--cache` stores that parse, keyed by commit id, parser version, and import roots. A hit skips that commit's tree list and parse. The 201-path run above and the 100005-line run in the Performance section are the measurements. |
| Output location | A directory junction on Windows, or a directory symlink on Linux, whose target is inside the repository is rejected with exit 2 and writes nothing. The same kind of link placed at `review-context.json` inside an otherwise outside directory is rejected, and the link target is not modified. |
| Read-only behavior | Index digests and `manage.py` bytes were unchanged. Repository hooks, `manage.py`, `package.json` scripts, and the migration file were not executed. |

## Defects corrected in this pass

- Transport-like revisions were still accepted as local ref text. They are now rejected without echoing the argument.
- Git stderr and syntax excerpts could retain URL userinfo, bearer tokens, and private keys. Those shapes are redacted before they reach errors or reports.
- The child environment already dropped unknown variables. The regression now fails if a credential variable is passed through.
- `credential.helper` from the repository is cleared on the Git command line. Empty aliases for the allowlisted subcommands are set as well. Built-in names such as `diff` are not overridden by a repository alias; the empty settings are defense in depth.
- `core.fsmonitor` was already cleared. A test now shows that the unprotected command runs a marker and the adapter does not.
- Report writes followed a reparse point at the destination file name. The writer now refuses a symlink or junction and replaces a normal file by renaming a new temporary file.

## Residual limitations

- The `git` binary on `PATH` is trusted. A substituted binary can ignore every flag.
- `protocol.allow=never` is not an operating-system firewall. Windows or WSL network policy is a separate control.
- `HOME` is still passed so Git can start. These commands do not ask Git to read a credential store, but a trusted Git binary could do so if one of them did.
- A reparse point created after the check and before the rename can still win that race.
- Angular and TypeScript stay diff-only. There is no component, template, or NgRx graph.
- Django model relations, attribute binding, and names bound inside functions are not resolved. Standard-library and third-party imports are unresolved unless those files sit under a configured `--python-root`.
- `from module import name` does not become a confirmed submodule. A name with no matching file is unresolved.
- Dynamic import targets are recorded only for a literal `import_module` or `__import__`, and those stay uncertain.
- Each Python file above 1 MiB is skipped. Files with NUL bytes are not parsed as source.
- Without `--cache`, both commits' Python trees are read on every run. With `--cache`, a hit skips that commit's tree list and parse. Disk use is capped at 64 MiB by dropping older records; one record larger than the cap is kept. Source bytes are read 32 blobs at a time. The measured working set is in the performance section. Memory still follows the parsed symbols of the two selected commits, not only the diff.
- The tool does not configure mTLS certificates or keys. Remote authentication stays with the approved Git client that fetched the repository before review.
- Offline-by-design does not delete reports. The output directory can contain paths and syntax excerpts and should be deleted when it is no longer needed.

## Performance

Package `0.4.0`. The reverse-import map is built in one walk. `--cache` is optional and source reads stay at 32 blobs at a time. Changed files consult the maps instead of scanning the tree again. The current suite result is in Commands and observed results.

100k-line measurement, synthetic only, from `python tests/profile_synthetic.py`. The generator writes 500 modules of 200 assignment lines and one changed importer. Observed line count `100005`, file count `501`. One file changed. Indexed path entries `1002` (501 paths in each of the two commits). Confirmed import edges `2`. Schema `1.1.0`. The cold report, the cache-fill report, and the cache-hit report were byte-identical.

Each figure is one measurement of a separate process. Wall-clock time covers `analyze` only. Peak RSS is `PeakWorkingSetSize` from `K32GetProcessMemoryInfo` for that process, so it includes interpreter startup. It is not an estimate.

| Run | Wall clock | Peak RSS |
| --- | --- | --- |
| Cold, no `--cache` | 1.767 s | 27,500,544 bytes |
| First run that writes `--cache` | 1.912 s | 58,949,632 bytes |
| Second run, same key | 1.504 s | 88,268,800 bytes |

The cache directory held 2 files and 15,448,080 bytes, under the 64 MiB cap. The hit is faster than the cold run. Its peak RSS is higher because both commit records are decoded at once; the cold run drops source bytes after each chunk of 32. Neither figure is a prediction for a 100k-line tree with heavier functions, nested classes, or a larger diff. Those shapes were not measured.
