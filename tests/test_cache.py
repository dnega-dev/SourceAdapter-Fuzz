from __future__ import annotations

import time
import unittest

from source_adapter_fuzz.cache import CacheEntry, MemoryCache, deduplicate_urls, normalize_cache_key


class CacheKeyTests(unittest.TestCase):
    def test_normalizes_scheme_host_default_port_and_fragment(self) -> None:
        self.assertEqual(
            normalize_cache_key("HTTP://LOCALHOST:80/path#display"),
            "http://localhost/path",
        )

    def test_sorts_query_components(self) -> None:
        self.assertEqual(
            normalize_cache_key("http://localhost/path?z=2&a=1"),
            "http://localhost/path?a=1&z=2",
        )

    def test_removes_dot_segments(self) -> None:
        self.assertEqual(
            normalize_cache_key("http://localhost/a/./b/../c"),
            "http://localhost/a/c",
        )

    def test_decodes_unreserved_percent_escape(self) -> None:
        self.assertEqual(
            normalize_cache_key("http://localhost/%7euser?q=%41"),
            "http://localhost/~user?q=A",
        )

    def test_preserves_duplicate_query_components(self) -> None:
        self.assertEqual(
            normalize_cache_key("http://localhost/x?a=2&a=1"),
            "http://localhost/x?a=1&a=2",
        )

    def test_non_default_port_is_retained(self) -> None:
        self.assertEqual(
            normalize_cache_key("http://LOCALHOST:8080/"),
            "http://localhost:8080/",
        )

    def test_relative_url_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_cache_key("/relative")

    def test_invalid_port_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_cache_key("http://localhost:not-a-port/")

    def test_deduplicate_keeps_first_spelling(self) -> None:
        urls = [
            "http://localhost/x?b=2&a=1",
            "http://LOCALHOST:80/x?a=1&b=2#fragment",
            "http://localhost/x?a=3",
        ]
        self.assertEqual(deduplicate_urls(urls), [urls[0], urls[2]])


class MemoryCacheTests(unittest.TestCase):
    def make_entry(self, key: str = "GET http://localhost/") -> CacheEntry:
        return CacheEntry(
            key=key,
            status_code=200,
            url="http://localhost/",
            headers={"etag": '"one"'},
            body=b"body",
            canonical_url="http://localhost/",
            stored_at=time.monotonic(),
        )

    def test_put_get_and_length(self) -> None:
        cache = MemoryCache()
        entry = self.make_entry()
        cache.put(entry)
        self.assertIs(cache.get(entry.key), entry)
        self.assertEqual(len(cache), 1)

    def test_delete(self) -> None:
        cache = MemoryCache()
        entry = self.make_entry()
        cache.put(entry)
        cache.delete(entry.key)
        self.assertIsNone(cache.get(entry.key))

    def test_clear(self) -> None:
        cache = MemoryCache()
        cache.put(self.make_entry("one"))
        cache.put(self.make_entry("two"))
        cache.clear()
        self.assertEqual(len(cache), 0)

    def test_freshness_requires_positive_max_age(self) -> None:
        entry = self.make_entry()
        self.assertFalse(entry.is_fresh(0.0))
        self.assertTrue(entry.is_fresh(10.0))

    def test_entry_exposes_validators(self) -> None:
        entry = self.make_entry()
        entry.headers["last-modified"] = "Wed, 01 Jan 2025 00:00:00 GMT"
        self.assertEqual(entry.etag, '"one"')
        self.assertIn("2025", entry.last_modified or "")


if __name__ == "__main__":
    unittest.main()
