# Angular scale benchmark

These numbers were measured on 2026-11-21 from a generated temporary repository. No company repository was read. The Python 100005-line cache measurement remains in `docs/validation.md` and was not repeated here.

## Machine

Windows 11. `platform.platform()` reported `Windows-11-10.0.26300-SP0`. Python 3.13.12. `git version 2.44.0.windows.1`. Peak RSS is the Windows working-set peak of the child process that performed the measured calls, including interpreter startup. This benchmark was not run under WSL.

## Fixture

`tests/profile_angular.py` writes 200 synthetic features, 1200 files, and 96999 lines. Each feature has a component, template, service, action, effect, and reducer. The component imports its service and the previous feature's service. The effect uses `ofType` on its imported action. The head commit adds one method to `src/app/feature-000/feature.component.ts`. The text is repetitive generated source, not an application, and it has no `node_modules`, tests, or build output.

The report budgets are the CLI defaults: depth 2, 40 files, 1048576 excerpt bytes, 200 lines per excerpt.

## Command

From the workspace root:

```powershell
$env:PYTHONPATH = "src"
python tests/profile_angular.py
```

The script prints the uncached phase timing, then the same phases with a TypeScript cache filled and read again in a new process, then one uncached report and two cached reports. unittest does not collect this module.

## Measured values

Paired run on one generated repository:

| Stage | Wall clock | Peak RSS | Git `cat-file` calls |
| --- | ---: | ---: | ---: |
| Index both commits, no cache | 28.510 s | 76832768 | 88 |
| Index both commits, cache fill | 15.790 s | 102096896 | 45 |
| Index both commits, cache hit | 1.005 s | 104095744 | 0 |
| Report, no cache | 30.094 s | 77590528 | (inside the report process) |
| Report, cache fill | 17.911 s | 103071744 | (inside the report process) |
| Report, cache hit | 3.073 s | 104861696 | (inside the report process) |

Graph construction stayed about 0.05 s and produced 1798 edges on head. A depth-8 walk from `src/app/feature-199/feature.component.ts` returned 4 paths in under 0.001 s. The walk is cheap because the chain is one service edge per step and the depth limit stops it. Indexing the blobs dominates.

The three reports were byte-identical (`REPORTS_MATCH True`). Each report was 11465 bytes, with schema `1.1.0`, `analysis_complete` false, `angular_semantic` `out_of_scope`, `angular_context.status` `partial`, 1 changed file, 3 selected excerpts, and 10 edges. The cache directory after the two cached reports held 5 files and 31021190 bytes.

An earlier uncached run, before the cache existed, on the same fixture shape was index 28.093 s at 74461184 bytes RSS and reports of 29.963 s and 31.406 s. Those reports also matched.

## Cache

`--cache` still stores the Python parse record. When the diff contains TypeScript or HTML, the same directory gains `typescript/`. A commit record is named by the commit object id, a digest of every indexed path and blob id, and extractor version `1`. A hit still runs `ls-tree` so that digest can be checked, and it does not batch-read blobs. A blob pack stores the path-independent scan of each blob id. The other commit in the same run, or a later commit that contains the same blob id, reuses that scan. A changed extractor version, a different commit, or a different blob digest misses. A mismatched pack is deleted and the blobs are read again.

Commit records are evicted oldest-first so the pack plus the records stay within 64 MiB when possible. The blob pack itself is kept. The measured directory was about 30 MB, under that cap. The report excerpt budget is unchanged.

The hit is faster and uses more RSS, because the process decodes the cached index for both commits at once. The cache does not reduce memory.

## Regression

After the cache was added:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

Observed: `Ran 73 tests in 129.433s` and `OK`. The cached and uncached reports of a small Angular fixture were byte-identical, and a stale extractor version in the blob pack caused the blobs to be read again.

## Limitations

- 96999 generated lines with repeated one-line methods. A real repository with large templates, generated code, or many changed files will not match these times.
- The lexical scan is not the TypeScript compiler. Template bindings are not indexed.
- Only one file changed, so the fill reuses almost every head blob. A diff that changes most files will stay close to the uncached time.
- Peak RSS is a process high-water mark, not a separate allocation trace.
- WSL timing was not measured for this fixture.
