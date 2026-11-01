"""Reddit via public RSS (free, no API key). Extracts $CASHTAGS and contract addresses."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from ..http import Http

ATOM = "{http://www.w3.org/2005/Atom}"
CASHTAG = re.compile(r"(?<![\w$])\$([A-Za-z][A-Za-z0-9]{1,11})\b")
SOL_ADDR = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
EVM_ADDR = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
TAG = re.compile(r"<[^>]+>")
# tickers that are almost always noise
STOP = {"USD", "USDT", "USDC", "SOL", "ETH", "BTC", "BNB", "THE", "AND"}


@dataclass
class Mention:
    post_id: str
    author: str
    symbols: set[str]
    addresses: set[str]


def extract(text: str) -> tuple[set[str], set[str]]:
    symbols = {m.upper() for m in CASHTAG.findall(text)} - STOP
    addrs = set(EVM_ADDR.findall(text))
    for cand in SOL_ADDR.findall(text):
        # base58 runs that are pure letters are almost always words/URLs, require a digit
        if any(c.isdigit() for c in cand):
            addrs.add(cand)
    return symbols, addrs


def parse_feed(xml_text: str) -> list[Mention]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for entry in root.findall(f"{ATOM}entry"):
        pid = entry.findtext(f"{ATOM}id") or ""
        author = (entry.find(f"{ATOM}author") or ET.Element("x")).findtext(f"{ATOM}name") or "?"
        title = entry.findtext(f"{ATOM}title") or ""
        content = TAG.sub(" ", html.unescape(entry.findtext(f"{ATOM}content") or ""))
        symbols, addrs = extract(f"{title} {content}")
        if symbols or addrs:
            out.append(Mention(pid, author, symbols, addrs))
    return out


async def fetch(http: Http, subreddit: str) -> list[Mention]:
    text = await http.get(f"https://www.reddit.com/r/{subreddit}/new/.rss?limit=100", as_text=True)
    return parse_feed(text) if text else []
