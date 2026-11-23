# Validation report

This report records the security and regression audit after the Angular context extension. It does not replace `docs/validation.md`. That file still describes the earlier 43-test run. The numbers below were observed on 2026-11-21 from synthetic temporary repositories only.

Package `0.4.0`. Report schema `1.1.0`. Phase `2-python`. Nested Angular context schema `1`.

## Commands and observed results

Windows 11, Python 3.13.12, `git version 2.44.0.windows.1`, from the workspace root:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

Observed: `Ran 71 tests in 145.984s` and `OK`.

The same discovery command inside `wsl -d Ubuntu-26.04`, with `python` resolving to CPython 3.13.12 and `git version 2.53.0`:

```bash
cd /mnt/c/work/00_src/ai-driven/offline-code-review-cursor
PYTHONPATH=src python -m unittest discover -s tests -v
```

Observed: `Ran 71 tests in 5.400s` and `OK`. Ubuntu's `python3` was not used. `wsl` without `-d` was not used, so the default `docker-desktop` distro was not the test host.

The focused adversarial test, run on Windows before the full suite, was:

```powershell
$env:PYTHONPATH = "src"
python -m unittest tests.test_security_regression -v
```

Observed: `Ran 1 test in 6.368s` and `OK`. The same test is included in both full-suite results above.

No test failed, so no implementation fix was required after these runs. The 100005-line cache measurement in `docs/validation.md` was not repeated for this audit.

## Confirmed

| Check | Evidence |
| --- | --- |
| No remote Git command is reachable from the adapter | `GitAdapterTests.test_blocks_network_subcommands`. The allowlist is `rev-parse`, `merge-base`, `diff`, `cat-file`, `ls-files`, `ls-tree`. `clone`, `fetch`, `pull`, `push`, `remote`, `ls-remote`, `submodule`, and `upload-pack` are rejected before `subprocess.run`. |
| No network client, plugin loader, or target-code execution in the package | Source inspection of `src/offline_review`. The only `subprocess.run` is in `git_adapter.py` with `shell=False`. There is no `shell=True`, `exec`, `eval`, `importlib`, `urllib`, `requests`, or socket client. |
| Sanitized Git child and no repo writes | `GitHardeningTests` covers the child environment, credential redaction on stderr, `diff.external`, `core.fsmonitor`, attribute diff drivers, junctions, and report symlinks. `output/` and `.cache/` are gitignored. Output and cache directories inside the work tree are rejected. |
| Revision provenance | Diffs use resolved object ids after `--end-of-options`. Transport-like revisions are rejected before Git. Python, TypeScript, and Angular tests read blobs and ignore a dirty work tree. |
| Deterministic reports | Repeated CLI runs in the integration, cache, context, and adversarial tests compare equal bytes. |
| Unicode and Windows paths | Diff parsing keeps a quoted UTF-8 rename and a path containing a space. The adversarial report selects `src/app/木.ts` on Windows and on WSL. |
| Binary, symlink, submodule, rename, deletion | Diff and hardening tests report binary files, symlink mode, submodule gitlinks, renames, and deletions. TypeScript mode `120000` and `160000` are warnings, not indexed blobs. LFS pointer text is recognized and not smudged. |
| Python analysis still present | `tests/test_python_analysis.py` ran inside both full suites, including both revisions from Git objects, deletion-only lines, and import certainty. |
| Fake imports are not confirmed edges | `ScanTests.test_comments_strings_and_regex_are_not_imports` and `AdversarialReportTests`. A comment import of `./secret.service`, a comment `@Component` pointing at `./fake.html`, and a string import of `./evil` do not create edges even when those files exist. The real `TreeService` import and `templateUrl` stay confirmed. |
| Malformed TypeScript is not guessed | An unclosed string and unbalanced braces produce status `unknown`, code `TS_UNCLOSED`, and no symbols. The adversarial report keeps `TS_UNCLOSED` on `src/app/broken.ts` and does not add an edge to `evil.ts`. |
| Path escape | `import { Outside } from '../../../../outside'` is `TS_IMPORT_UNRESOLVED`. No selected or edge path is absolute or contains `..`. The test repository did not gain `outside.ts`. |
| Cycles and budgets | `GraphTests.test_cycle_stops` stops on a repeated path. `--max-depth 0` keeps changed files only. `--max-files`, `--max-bytes`, and `--max-lines-per-excerpt` record `omitted` and `coverage.truncated`. Negative values exit 2. |
| Secret-shaped excerpt text | A bearer token in a changed comment is absent from both report files. URL userinfo, bearer tokens, and private keys are covered by `RedactionTests`. |
| Existing JSON keys | Schema remains `1.1.0`. `coverage.angular_semantic` remains `out_of_scope`. A Python-only change can still be `analysis_complete` true, with `angular_context.status` `not_applicable`. |

## Limitations

- TypeScript support is a conservative lexical scan. `angular_context.status` is `partial` or `not_applicable`. `coverage.typescript_semantic` is `partial` only when a `.ts`, `.tsx`, or `.html` path is in the diff. Template bindings are `unsupported`.
- `coverage.angular_semantic` stays `out_of_scope`, and a frontend diff still adds `angular_semantic_out_of_scope`. Lexical results do not set top-level `analysis_complete` to true.
- Selector text, `styleUrls`, dynamic import, and `tsconfig` `extends` do not create confirmed edges. A root `tsconfig.json` blob is the only alias source. JSON comments in that file are unreadable.
- An NgModule declaration that is not imported is only a candidate, and only when one class in the commit has that name.
- Changed-line excerpts quote source text. A fake import that appears in a changed comment or string can therefore appear in the excerpt. It is not turned into an edge. Bearer tokens, URL userinfo, assigned secrets, and private keys are redacted. An ordinary identifier such as `SECRET_KEY` is not.
- A file larger than 1,048,576 bytes is `too_large` at the scanner, and the commit indexer skips the blob when Git metadata already reports that size. This audit did not add a 1 MiB Git fixture.
- CSS and SCSS remain diff metadata. They are not indexed and do not get Angular edges.
- `protocol.allow=never`, the empty credential helper, and the subcommand allowlist are process-local. They are not an operating-system firewall. This audit did not change host firewall or WSL networking settings.
- The tool does not configure mTLS certificates or keys. Remote authentication stays outside this command.
- The Python parse cache does not store TypeScript indexes. A diff with no `.ts`, `.tsx`, or `.html` path does not list the tree for TypeScript.
