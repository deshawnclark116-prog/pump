"""CLI: python -m memeradar.attention {status,collect,train,backtest,delete-post}."""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import json
import os

from .db import connect, brief, remove_post
from .model import train, source_lags
from .backtest import simulate


def utc(s):
    return datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()


def main():
    p=argparse.ArgumentParser(description="Receipt-time Solana attention research, paper only")
    p.add_argument("--db",default=os.environ.get("MEMERADAR_DB","memeradar.db"))
    cmds=p.add_subparsers(dest="cmd",required=True)
    cmds.add_parser("status")
    cmds.add_parser("collect")
    cmds.add_parser("train")
    cmds.add_parser("lag-report")
    b=cmds.add_parser("backtest")
    b.add_argument("--model",required=True)
    b.add_argument("--since",required=True,type=utc)
    b.add_argument("--until",required=True,type=utc)
    d=cmds.add_parser("delete-post")
    d.add_argument("--source",required=True,choices=["x","telegram","reddit"])
    d.add_argument("--id",required=True)
    args=p.parse_args()
    db=connect(args.db)
    if args.cmd=="status":
        result=brief(db)
    elif args.cmd=="train":
        result=train(db)
    elif args.cmd=="lag-report":
        result=source_lags(db)
    elif args.cmd=="backtest":
        if args.since>=args.until:
            p.error("since must be before until")
        result=simulate(db,args.model,since=args.since,until=args.until)
    elif args.cmd=="delete-post":
        result={"deleted":remove_post(db,args.source,args.id),
                "warning":"Retrain any model that incorporated a deleted post"}
    else:
        from ..http import Http
        from .runner import run_collectors
        import asyncio
        async def go():
            http=Http()
            try:
                await run_collectors(http,db)
            finally:
                await http.close()
        asyncio.run(go())
        return
    print(json.dumps(result,indent=2,allow_nan=False))


if __name__=="__main__":
    main()
