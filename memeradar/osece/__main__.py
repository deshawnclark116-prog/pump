"""OSECE status/individual paper trades: python -m memeradar.osece [--db PATH] report."""
from __future__ import annotations

import argparse
import json
import os

from ..attention.db import connect
from .core import report


def main():
    parser=argparse.ArgumentParser(description="OSECE independent forward paper ledger")
    parser.add_argument("--db",default=os.environ.get("MEMERADAR_DB","memeradar.db"))
    parser.add_argument("command",nargs="?",choices=("report","status"),default="report")
    args=parser.parse_args()
    db=connect(args.db)
    print(json.dumps(report(db),indent=2,allow_nan=False))


if __name__=="__main__":
    main()
