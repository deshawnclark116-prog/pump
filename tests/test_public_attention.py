"""Credential-free attention collectors: contract identity, timing, and provenance."""
import asyncio
from datetime import datetime, timezone

from memeradar.attention import db as evidence, model, public_sources as pub

MINT = "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"


def db():
    return evidence.connect(":memory:")


def test_public_social_posts_are_timestamped_deduped_and_stale_filtered():
    c = db()
    now = 2000
    stamp = datetime.fromtimestamp(now - 60, timezone.utc).isoformat()
    class Http:
        async def get(self, url, **kw):
            if url == pub.BLUESKY:
                return {"posts": [
                    {"uri": "at://did:plc:a/app.bsky.feed.post/1",
                     "record": {"text": "SOL token CA " + MINT, "createdAt": stamp},
                     "likeCount": 2, "replyCount": 3},
                    {"uri": "at://did:plc:a/app.bsky.feed.post/2",
                     "record": {"text": "$MINT with no contract", "createdAt": stamp}},
                    {"uri": "at://did:plc:a/app.bsky.feed.post/3",
                     "record": {"text": MINT, "createdAt": "1970-01-01T00:00:01Z"}},
                ]}
            raise AssertionError(url)
    assert asyncio.run(pub.bluesky(Http(), c, now=now)) == 1
    assert asyncio.run(pub.bluesky(Http(), c, now=now+120)) == 0
    r = c.execute("SELECT * FROM attention_hits").fetchone()
    assert r["source"] == "bluesky"
    assert r["observed_ts"] == now
    assert r["posted_ts"] == now - 60
    assert r["engagement"] == 5
    assert c.execute("SELECT COUNT(*) FROM attention_hits").fetchone()[0] == 1


def test_biz_catalog_html_and_conditional_no_change():
    c = db()
    now = 10000
    class Http:
        def __init__(self):
            self.calls = []
        async def get(self, url, **kw):
            self.calls.append(kw)
            if len(self.calls) == 2:
                return {"_not_modified": True}
            return [{"page": 1, "threads": [{
                "no": 11, "time": now-100, "sub": "Solana",
                "com": "<b>CA</b> " + MINT,
                "last_replies": [
                    {"no": 12, "time": now-30, "com": "buy $SOL no contract"},
                    {"no": 13, "time": now-2000, "com": MINT}]}]}]
    h = Http()
    assert asyncio.run(pub.biz(h, c, now=now)) == 1
    assert asyncio.run(pub.biz(h, c, now=now+600)) == 0
    assert "If-Modified-Since" in h.calls[1]["headers"]
    assert c.execute("SELECT count(*) FROM attention_hits").fetchone()[0] == 1
    assert c.execute("SELECT status FROM attention_source_state WHERE source='4chan_biz'").fetchone()[0] == "not_modified"


def test_trend_sources_store_rank_only_and_verify_mint_identity():
    c = db()
    now = 10000
    class Http:
        async def get(self, url, **kw):
            if url == pub.GECKO:
                return {"data": [
                    {"id": "solana_pool1", "relationships": {
                        "base_token": {"data": {"id": "solana_" + MINT}}}},
                    {"id": "ethereum_pool2", "relationships": {
                        "base_token": {"data": {"id": "eth_" + MINT}}}}]}
            if url == pub.COINGECKO:
                return {"coins": [{"item": {"id": "unverified-token", "symbol": "SAME"}}]}
            raise AssertionError(url)
    h = Http()
    assert asyncio.run(pub.gecko_trending(h, c, now=now)) == 1
    assert asyncio.run(pub.coingecko_trending(h, c, now=now)) == 1
    assert evidence.active_mints(c, now=now+30) == [MINT]
    assert c.execute("SELECT COUNT(*) FROM attention_hits").fetchone()[0] == 0
    rows = c.execute("SELECT source,mint,rank FROM attention_trends ORDER BY source,rank").fetchall()
    assert len(rows) == 3
    assert any(r["source"] == "coingecko_trending" and r["mint"] is None for r in rows)
    assert any(r["source"] == "geckoterminal_trending" and r["mint"] == MINT and r["rank"] == 1 for r in rows)


def test_unavailable_feeds_fail_closed_and_do_not_create_signals():
    c = db()
    class Http:
        async def get(self, url, **kw):
            return None
    assert asyncio.run(pub.poll_public(Http(), c)) == {
        "bluesky": 0, "4chan_biz": 0, "geckoterminal_trending": 0,
        "coingecko_trending": 0}
    assert evidence.brief(c)["attention_hits"] == 0
    assert evidence.brief(c)["attention_trends"] == 0
    assert {r["status"] for r in evidence.brief(c)["sources"]} == {"error"}


def test_model_keeps_gecko_market_attention_distinct_from_social():
    assert "bluesky" in model.SOURCE_ORDER
    assert "4chan_biz" in model.SOURCE_ORDER
    assert "geckoterminal_trending" in model.SOURCE_ORDER
    row = {"observed_ts":1000, "buys_m5":10, "sells_m5":10,
           "price_usd":1.0, "buy_volume_proxy_m5":200, "liquidity_usd":20000}
    hits = [
        {"source":"bluesky", "observed_ts":999, "engagement":3, "audience":None},
        {"source":"geckoterminal_trending", "observed_ts":999, "engagement":None, "audience":None}]
    x = model.features_for(row, hits)
    assert len(x) == len(model.FEATURES)
    assert x[model.SOURCE_ORDER.index("bluesky")] == 1
    assert x[model.SOURCE_ORDER.index("geckoterminal_trending")] == 1
    assert x[model.SOURCE_ORDER.index("reddit")] == 0
    assert model.forecast({"features": ["obsolete"], "coef":[1], "mean":[0],
                           "scale":[1], "intercept":100}, x) == 0.0
