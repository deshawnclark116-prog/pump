"""Minimal local dashboard (aiohttp, no Grafana/Next.js needed): http://127.0.0.1:8080"""
from __future__ import annotations

import asyncio
import json
import time

from aiohttp import web

from .config import Config
from .store import Store

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Meme Radar</title>
<style>
:root{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#e5e5e5;--hi:#0a7d32}
@media(prefers-color-scheme:dark){:root{--bg:#121212;--fg:#eee;--muted:#999;--line:#2a2a2a;--hi:#4ade80}}
body{background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,sans-serif;margin:0;padding:16px}
h1{font-size:18px;margin:0 0 4px} p{color:var(--muted);margin:0 0 12px}
.wrap{overflow-x:auto} table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left} th{color:var(--muted);font-weight:500}
.score{font-weight:600} .st-EARLY{color:var(--hi);font-weight:600} .st-LATE,.st-DUMPING{color:#d33} .hot{color:var(--hi)} a{color:inherit}
</style></head><body>
<h1>Meme Radar</h1><p id="meta">loading…</p>
<div class="wrap"><table><thead><tr><th>Token</th><th>Score</th><th>Social</th><th>Volume</th>
<th>Stage</th><th>Run-up</th><th>Buyers</th><th>Liq</th><th>Liquidity</th><th>Vol 1h</th><th>MCap</th><th>Seen</th></tr></thead>
<tbody id="rows"></tbody></table></div>
<script>
const usd=x=>x==null?"–":x>=1e6?"$"+(x/1e6).toFixed(1)+"M":x>=1e3?"$"+(x/1e3).toFixed(1)+"k":"$"+x.toFixed(0);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function load(){
  const r=await fetch("api/tokens");const d=await r.json();
  document.getElementById("meta").textContent=`${d.tokens.length} safe tokens · threshold ${d.threshold} · updated ${new Date().toLocaleTimeString()}`;
  document.getElementById("rows").innerHTML=d.tokens.map(t=>{const f=JSON.parse(t.features||"{}");
    const ago=Math.round((Date.now()/1000-t.ts)/60);
    return `<tr><td><a href="https://dexscreener.com/${esc(t.chain)}/${esc(t.address)}" target="_blank" rel="noopener">$${esc(t.symbol||"?")}</a> <small>${esc(t.chain)}</small></td>
    <td class="score ${t.score>=d.threshold?"hot":""}">${t.score.toFixed(2)}</td>
    <td>${(f.social_velocity||0).toFixed(2)}</td><td>${(f.volume_acceleration||0).toFixed(2)}</td>
    <td class="st-${esc(f.stage)}">${esc(f.stage||"–")}</td><td>${f.runup?f.runup.toFixed(1)+"x":"–"}</td>
    <td>${(f.wallet_diversity||0).toFixed(2)}</td><td>${(f.liquidity_depth||0).toFixed(2)}</td><td>${usd(t.liquidity_usd)}</td><td>${usd(t.vol_h1)}</td>
    <td>${usd(t.market_cap)}</td><td>${ago}m ago</td></tr>`}).join("");
}
load();setInterval(load,15000);
</script></body></html>"""


def make_app(store: Store, cfg: Config) -> web.Application:
    async def index(_):
        return web.Response(text=PAGE, content_type="text/html")

    async def tokens(_):
        return web.json_response({"threshold": cfg.alert_threshold, "now": time.time(),
                                  "tokens": store.leaderboard(), "alerts": store.recent_alerts()},
                                 dumps=lambda o: json.dumps(o, default=str))

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/tokens", tokens)
    return app


async def serve(store: Store, cfg: Config) -> None:
    runner = web.AppRunner(make_app(store, cfg))
    await runner.setup()
    await web.TCPSite(runner, cfg.dashboard_host, cfg.dashboard_port).start()
    print(f"dashboard: http://{cfg.dashboard_host}:{cfg.dashboard_port}")
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
