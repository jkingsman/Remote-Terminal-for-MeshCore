"""Tests for feed parsing, item selection, and format templating."""

from app.bots.feeds import (
    extract_api_items,
    format_item,
    parse_rss,
    select_new_items,
    validate_feed_url,
)

RSS_BODY = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Blog</title>
<item><title>Post Two</title><link>https://ex.com/2</link><guid>g2</guid>
  <description>Second</description><pubDate>Fri, 21 Aug 2026 10:00:00 GMT</pubDate></item>
<item><title>Post One</title><link>https://ex.com/1</link><guid>g1</guid>
  <description>First</description></item>
</channel></rss>"""

ATOM_BODY = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title>
<entry><id>a2</id><title>Entry Two</title>
  <link rel="alternate" href="https://ex.com/a2"/><summary>S2</summary></entry>
<entry><id>a1</id><title>Entry One</title>
  <link href="https://ex.com/a1"/><content>C1</content></entry>
</feed>"""


class TestParsing:
    def test_rss2(self):
        items = parse_rss(RSS_BODY)
        assert [i["id"] for i in items] == ["g2", "g1"]
        assert items[0]["title"] == "Post Two"
        assert items[0]["link"] == "https://ex.com/2"

    def test_atom(self):
        items = parse_rss(ATOM_BODY)
        assert [i["id"] for i in items] == ["a2", "a1"]
        assert items[1]["summary"] == "C1"

    def test_api_items_path(self):
        payload = {"data": {"results": [{"id": 5, "title": "x"}, {"guid": "y"}]}}
        items = extract_api_items(payload, "data.results")
        assert items[0]["id"] == "5"
        assert items[1]["id"] == "y"


class TestSelection:
    def test_first_check_posts_nothing(self):
        items = parse_rss(RSS_BODY)
        result = select_new_items(items, None, max_posts=3)
        assert result.new_items == []
        assert result.newest_id == "g2"

    def test_new_items_oldest_first_and_capped(self):
        items = [{"id": f"i{n}"} for n in range(6, 0, -1)]  # i6 newest ... i1 oldest
        result = select_new_items(items, "i1", max_posts=3)
        # 5 unseen (i6..i2); capped to the 3 NEWEST, posted oldest-first
        assert [i["id"] for i in result.new_items] == ["i4", "i5", "i6"]
        assert result.newest_id == "i6"

    def test_no_new_items(self):
        items = parse_rss(RSS_BODY)
        result = select_new_items(items, "g2", max_posts=3)
        assert result.new_items == []


class TestFormatting:
    def test_placeholders_and_filters(self):
        item = {"title": "A very long headline indeed", "link": "https://ex.com/x"}
        out = format_item("[Blog] {title|truncate:14}\\n{link}", item)
        assert out.startswith("[Blog] A very long")
        assert out.endswith("https://ex.com/x")
        assert "\n" in out

    def test_unknown_field_renders_empty(self):
        assert format_item("x{nope}y", {}) == "xy"

    def test_filter_chain(self):
        assert format_item("{t|strip|upper}", {"t": "  hi  "}) == "HI"


class TestSsrfGuard:
    def test_private_hosts_blocked(self):
        assert validate_feed_url("http://127.0.0.1/feed") is not None
        assert validate_feed_url("http://192.168.1.10/feed") is not None
        assert validate_feed_url("ftp://example.com/feed") is not None
        assert validate_feed_url("https://") is not None
