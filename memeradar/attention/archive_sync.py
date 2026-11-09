"""Recover the previous artifact even when GitHub's runner cache save fails.

GitHub Actions already uploads a read-only research artifact after each
six-hour scan. It is an immutable recovery source, not a source of invented
trades. We reconcile archived trade receipts by stable identity; never copy
raw autoincrement IDs from forked DBs into the current DB.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile

from .archive_recovery import reconcile


def gh_api(path, outfile=None):
    cmd=["gh","api",path]
    if outfile is None:
        proc=subprocess.run(cmd,capture_output=True,timeout=45,check=True)
        return json.loads(proc.stdout)
    with open(outfile,"wb") as out:
        subprocess.run(cmd,stdout=out,stderr=subprocess.PIPE,timeout=150,check=True)


def restore(db_path,repo,limit=2):
    if not repo or "/" not in repo:
        raise ValueError("GitHub owner/repo required")
    reply=gh_api(f"repos/{repo}/actions/artifacts?per_page=30")
    found=sorted(
        (x for x in reply.get("artifacts",[])
         if x.get("name","").startswith("research-") and
         not x.get("expired") and x.get("size_in_bytes",0)>0),
        key=lambda x:x.get("created_at",""),reverse=True)
    # Always include the November 9 lost-history run (artifact ID known
    # from successful export). Ignore if artifact expired in future.
    known={x["id"]:x for x in found[:limit]}
    legacy_id=13656055803
    if all(x["id"]!=legacy_id for x in known.values()):
        known[legacy_id]={"id":legacy_id,"name":"research-42545065003"}
    stats=[]
    with tempfile.TemporaryDirectory(prefix="meme_archive_") as temp:
        for x in sorted(known.values(),key=lambda z:z["id"]):
            run=x["name"].split("research-")[-1]
            if Path(db_path).exists():
                with sqlite3.connect(db_path) as check:
                    prior=check.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='paper_archive_recovery'").fetchone()
                    if prior and check.execute(
                            "SELECT 1 FROM paper_archive_recovery WHERE run_id=?",
                            (run,)).fetchone():
                        stats.append({"run_id":run,"skipped":"previously recovered"})
                        continue
            zip_path=Path(temp)/f"{x['id']}.zip"
            source_path=Path(temp)/f"{x['id']}.db"
            try:
                gh_api(f"repos/{repo}/actions/artifacts/{x['id']}/zip",zip_path)
                with zipfile.ZipFile(zip_path) as z:
                    with z.open("memeradar.db") as source,open(source_path,"wb") as dst:
                        shutil.copyfileobj(source,dst,1024*1024)
                zip_path.unlink(missing_ok=True)
                src=sqlite3.connect(f"file:{source_path}?mode=ro",uri=True)
                src.row_factory=sqlite3.Row
                if src.execute("PRAGMA quick_check").fetchone()[0]!="ok":
                    raise RuntimeError("archived SQLite quick_check failed")
                if not Path(db_path).exists():
                    shutil.copy2(source_path,db_path)
                    print(f"ARCHIVE_RESTORE restored full DB from {run}")
                dest=sqlite3.connect(db_path)
                dest.row_factory=sqlite3.Row
                try:
                    result=reconcile(dest,src,run)
                    stats.append(result)
                    print("ARCHIVE_RECONCILE "+json.dumps(result,sort_keys=True))
                finally:
                    dest.close()
                    src.close()
            except (OSError,KeyError,ValueError,RuntimeError,
                    subprocess.CalledProcessError,subprocess.TimeoutExpired,
                    zipfile.BadZipFile,sqlite3.Error) as ex:
                stats.append({"run_id":run,"error":type(ex).__name__})
                print(f"::warning::Could not reconcile artifact {run}: {type(ex).__name__}")
    return stats


def main():
    p=argparse.ArgumentParser(description="Merge prior immutable paper trade receipts")
    p.add_argument("--db",default="memeradar.db")
    p.add_argument("--repo",default=os.environ.get("GITHUB_REPOSITORY"))
    p.add_argument("--latest",type=int,default=2)
    args=p.parse_args()
    print(json.dumps(restore(args.db,args.repo,args.latest),sort_keys=True))


if __name__=="__main__":
    main()
