"""Convenience entry point so the pipeline can be run from the repo root:

    python run_pipeline.py --offline --decision approve

Equivalent to `python -m pulse.run_pipeline` with src/ on the path.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from pulse.run_pipeline import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
