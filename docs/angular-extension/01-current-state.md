# Current-state baseline (from supplied JSON excerpt)

The existing tool generated a direct/two-dot Git diff report between base `7e85d3ecf4e2e584a04a2a3fc930ab75a3f9a205` and head `c034d66c1a28543160ae6e0f432f0c47c33ad8a1`.

| Field | Observed |
|---|---|
| files_total | 21 |
| files_added / files_modified | 3 / 18 |
| hunks_total | 234 |
| addition_hunks / edit_hunks / deletion_hunks | 58 / 167 / 9 |
| errors | [] |
| angular_semantic | out_of_scope |
| typescript_html | diff_only |
| analysis_complete / partial | false / true |
| partial_reasons | [angular_semantic_out_of_scope] |
| python_ast | not_applicable |

Example added files: `src/app/product-tree/components/product-tree-filter/product-tree-filter.component.ts`, matching `.html` and `.scss`. Example modified files include `product-info-panel.component.ts`, `.html`, `product-api.service.ts`, and `docs/製品ツリー画面仕様書.md`.

**Important:** This report proves diff metadata extraction, not semantic analysis, dependency extraction, code correctness or AI review. The supplied JSON includes changed line numbers and blob IDs, but not the source text necessary to validate behavior. Cursor must inspect the existing tool's actual code in a nonconfidential development copy before implementing extensions.
