"""P1-13: read-only audit of source-to-after-checkpoint local timing proofs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from infrastructure.spot_checkpoint_delivery_probe import inspect, PASS


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    a = p.parse_args(argv)
    try:
        result = inspect(a.root)
    except (ValueError, OSError, KeyError, TypeError, IndexError,
            sqlite3.Error, json.JSONDecodeError) as err:
        result = {
            "verdict": "REFUSED_INDEPENDENT_CHECKPOINT_COMPLETION_AGE",
            "failure_class": type(err).__name__,
            "orders_submitted": 0,
        }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["verdict"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
