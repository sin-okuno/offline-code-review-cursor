"""Allow ``python -m offline_review`` to run the CLI."""

from __future__ import annotations

import sys

from offline_review.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
