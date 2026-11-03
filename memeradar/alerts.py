"""Free alert sinks: ntfy app, Discord webhook, Telegram bot."""
from __future__ import annotations

import logging

from .config import Config
from .http import Http
from .models import TokenState

log = logging.getLogger(__name__)


def _usd(x: float | None) -> str:
    if x is None:
        return "?"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(x) >= div:
            return f"${x / div:.1f}{suf}"
    return f"${x:.0f}"


def format_alert(t: TokenState) -> str:
    f = t.features
    age = f"{t.age_minutes:.0f}m" if t.age_minutes is not None else "?"
    link = f"https://dexscreener.com/{t.chain}/{t.pair_address or t.address}"
    st = t.stage
    stage_line = ""
    if st:
        icon = {"EARLY": "🟢", "RUNNING": "🟡"}.get(st.stage, "🔴")
        bits = [f"{icon} {st.stage}"]
        if st.runup:
            bits.append(f"{st.runup:.1f}x since launch")
        if st.drawdown:
            bits.append(f"{st.drawdown:.0%} off peak")
        if t.buyers:
            bits.append(f"buyers 5m {t.buyers.get('m5', 0)} / sellers {(t.sellers or {}).get('m5', 0)}")
        stage_line = " · ".join(bits) + (f"\n({'; '.join(st.reasons)})" if st.reasons else "") + "\n"
    return (
        f"🚀 ${t.symbol or '?'} ({t.name}) on {t.chain} — score {t.score:.2f}\n"
        f"{stage_line}"
        f"age {age} · liq {_usd(t.liquidity_usd)} · mcap {_usd(t.market_cap)} · "
        f"vol 5m {_usd(t.vol_m5)} / 1h {_usd(t.vol_h1)}\n"
        f"social {f.get('social_velocity', 0):.2f} · volume {f.get('volume_acceleration', 0):.2f} · "
        f"buyers {f.get('wallet_diversity', 0):.2f} · liq {f.get('liquidity_depth', 0):.2f}\n"
        f"top10 {t.safety.top10_pct if t.safety else '?'}% · holders {t.safety.holders if t.safety else '?'}\n"
        f"{t.address}\n{link}\n"
        f"Not financial advice. Safety checks reduce, not remove, rug risk."
    )


def ntfy_url(topic: str) -> str:
    topic = topic.strip()
    return topic if topic.startswith(("http://", "https://")) else f"https://ntfy.sh/{topic}"


def chart_link(chain: str, pair_or_address: str) -> str:
    return f"https://dexscreener.com/{chain}/{pair_or_address}"


def _ascii(s: str) -> str:
    return s.encode("ascii", "ignore").decode().strip()


async def send(http: Http, cfg: Config, t: TokenState) -> None:
    await send_text(http, cfg, format_alert(t), symbol=t.symbol, address=t.address,
                    link=chart_link(t.chain, t.pair_address or t.address), kind="NEW")


async def send_text(http: Http, cfg: Config, text: str, *, symbol: str | None = None,
                    address: str | None = None, link: str | None = None, kind: str = "") -> None:
    """symbol/address/link make the phone notification easy to act on: the title shows the
    exact coin (ticker + end of its contract address), tapping opens its chart, and there is a
    one-tap "Copy address" button to paste into any trading app's search."""
    sent = False
    if cfg.ntfy_topic:
        # entry + red alerts buzz loudly (high priority); take-profit/fading are normal
        prio = "high" if text.startswith(("🚀", "🔴", "✅")) else "default"
        headers = {"Priority": prio}
        if address:
            title = f"{kind} ${_ascii(symbol or '?')} - address ends ...{address[-8:]}".strip()
            headers["Title"] = title[:200]
            actions = [f"copy, Copy address, {address}"]
            if link:
                actions.append(f"view, Open chart, {link}")
            headers["Actions"] = "; ".join(actions)
        if link:
            headers["Click"] = link
        await http.post(ntfy_url(cfg.ntfy_topic), data=text[:3900].encode(),
                        headers=headers, retries=1)
        sent = True
    if cfg.discord_webhook_url:
        await http.post(cfg.discord_webhook_url, json={"content": text[:1990]}, retries=1)
        sent = True
    if cfg.telegram_bot_token and cfg.telegram_chat_id:
        await http.post(f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage",
                        json={"chat_id": cfg.telegram_chat_id, "text": text,
                              "disable_web_page_preview": True}, retries=1)
        sent = True
    if not sent:
        log.info("ALERT\n%s", text)
