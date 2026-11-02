"""CLI: python -m memeradar {run,backtest,tune}"""
from __future__ import annotations

import argparse
import asyncio
import logging

from . import backtest
from .config import load_config
from .pipeline import Radar
from .store import Store


async def report(cfg) -> None:
    import time
    from .http import Http
    from .sources import dexscreener

    store = Store(cfg.db_path)
    alerts = list(reversed(store.recent_alerts(500)))
    if not alerts:
        print("no alerts yet")
        return
    positions = {(p["chain"], p["address"], p["entry_ts"]): p for p in store.load_positions(open_only=False)}
    http = Http()
    try:
        live: dict[tuple[str, str], dict] = {}
        by_chain: dict[str, list[str]] = {}
        for a in alerts:
            by_chain.setdefault(a["chain"], []).append(a["address"])
        for chain, addrs in by_chain.items():
            for addr, snap in (await dexscreener.snapshots(http, chain, sorted(set(addrs)))).items():
                live[(chain, addr)] = snap
    finally:
        await http.close()

    for a in alerts:
        entry = a["price_usd"]
        pos = positions.get((a["chain"], a["address"], a["ts"])) or {}
        peak = max(filter(None, [pos.get("peak_price"), store.max_price_since(a["chain"], a["address"], a["ts"]), entry]))
        now_price = (live.get((a["chain"], a["address"])) or {}).get("price_usd")
        pct = lambda p: f"{(p / entry - 1) * 100:+.0f}%" if p and entry else "?"  # noqa: E731
        when = time.strftime("%m-%d %H:%M", time.localtime(a["ts"]))
        print(f"{when}  ${a['symbol']:<12} best after alert {pct(peak):>6}   now {pct(now_price):>6}")
        for e in store.exit_events_for(a["chain"], a["address"], a["ts"]):
            t = time.strftime("%H:%M", time.localtime(e["ts"]))
            print(f"      {t} {e['title']}: {e['detail']} (at {e['change_pct']:+.0f}%)")


def main() -> None:
    ap = argparse.ArgumentParser(prog="memeradar", description="Free meme-coin momentum radar")
    ap.add_argument("--config", help="path to config.toml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run the radar")
    run.add_argument("--duration", type=float, help="stop after N seconds (for cron / GitHub Actions)")
    run.add_argument("--dashboard", action="store_true", help="serve the local dashboard")
    run.add_argument("--no-pumpportal", action="store_true", help="disable the pump.fun websocket")

    sub.add_parser("report", help="every alert so far and what it did afterwards")

    for name in ("backtest", "tune"):
        p = sub.add_parser(name, help=f"{name} scoring weights on collected snapshots")
        p.add_argument("--threshold", type=float, help="score threshold (default: config alert_threshold)")
        p.add_argument("--horizon", type=float, default=60, help="forward window in minutes")

    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    cfg = load_config(args.config)

    if args.cmd == "run":
        if args.no_pumpportal:
            cfg.enable_pumpportal = False
        try:
            asyncio.run(Radar(cfg).run(duration=args.duration, dashboard=args.dashboard))
        except KeyboardInterrupt:
            pass
        return

    if args.cmd == "report":
        asyncio.run(report(cfg))
        return

    threshold = args.threshold if args.threshold is not None else cfg.alert_threshold
    rows = backtest.build_rows(Store(cfg.db_path).all_snapshots(), args.horizon * 60)
    print(f"{len(rows)} snapshots with forward prices")
    if args.cmd == "backtest":
        for th in sorted({0.3, 0.4, 0.5, 0.6, 0.7, threshold}):
            m = backtest.evaluate(rows, cfg.weights.as_dict(), th)
            print(f"threshold {th:.2f}: {m['alerts']:4d} alerts, hit-rate(+50%) {m['hit_rate']:.0%}, "
                  f"median max-return {m['median_fwd_max']:+.0%}")
    else:
        w, m = backtest.tune(rows, threshold)
        if not w:
            print("not enough data yet – let the radar run longer")
            return
        print(f"best on {m['alerts']} alerts: hit-rate {m['hit_rate']:.0%}, median {m['median_fwd_max']:+.0%}")
        print("[weights]")
        for k, v in w.items():
            print(f"{k} = {v}")


if __name__ == "__main__":
    main()
