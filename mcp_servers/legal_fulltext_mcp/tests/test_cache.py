from mcp_servers.legal_fulltext_mcp.infrastructure.cache import SQLiteTTLCache


def test_cache_round_trip(tmp_path):
    cache = SQLiteTTLCache(str(tmp_path / "cache.sqlite3"), ttl_seconds=3600, enabled=True)
    cache.set("key", {"found": True})
    assert cache.get("key") == {"found": True}
