"""Deterministic in-memory cache and URL cache-key normalization."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional
from urllib.parse import quote, urlsplit, urlunsplit


_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_PERCENT = re.compile(r"%([0-9a-fA-F]{2})")
_DEFAULT_PORT = {"http": 80, "https": 443}


def _normalize_percent(value: str) -> str:
    def replace(match: re.Match) -> str:
        byte = int(match.group(1), 16)
        char = chr(byte)
        if char in _UNRESERVED:
            return char
        return "%{:02X}".format(byte)

    return _PERCENT.sub(replace, value)


def _remove_dot_segments(path: str) -> str:
    """RFC 3986 dot-segment removal without collapsing meaningful ``//``."""

    input_path = path
    output: List[str] = []
    absolute = input_path.startswith("/")
    trailing = input_path.endswith("/") or input_path.endswith("/.") or input_path.endswith("/..")
    for segment in input_path.split("/"):
        if segment in ("", "."):
            if segment == "" and output and output[-1] != "":
                output.append("")
            continue
        if segment == "..":
            while output and output[-1] == "":
                output.pop()
            if output:
                output.pop()
            continue
        output.append(segment)
    result = "/".join(output)
    if absolute and not result.startswith("/"):
        result = "/" + result
    if not result:
        result = "/" if absolute else ""
    if trailing and result != "/" and not result.endswith("/"):
        result += "/"
    return result


def _normalize_query(query: str) -> str:
    if not query:
        return ""
    # Sort raw components so duplicate keys and unusual encodings remain lossless.
    parts = [_normalize_percent(part) for part in query.split("&")]
    return "&".join(sorted(parts))


def normalize_cache_key(url: str) -> str:
    """Return a conservative normalized URL suitable for cache/dedupe keys.

    The fragment is removed; scheme/host are lower-cased; default ports, dot
    segments, and unreserved percent escapes are normalized; query components
    are sorted. User-info is preserved but never logged by the reference CLI.
    """

    split = urlsplit(url.strip())
    scheme = split.scheme.lower()
    hostname = split.hostname
    if not scheme or hostname is None:
        raise ValueError("absolute URL with scheme and host required")
    try:
        host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("invalid internationalized hostname") from exc
    if ":" in host and not host.startswith("["):
        host = "[{}]".format(host)
    try:
        port = split.port
    except ValueError as exc:
        raise ValueError("invalid URL port") from exc
    userinfo = ""
    if split.username is not None:
        userinfo = quote(split.username, safe="%")
        if split.password is not None:
            userinfo += ":" + quote(split.password, safe="%")
        userinfo += "@"
    port_text = ""
    if port is not None and _DEFAULT_PORT.get(scheme) != port:
        port_text = ":{}".format(port)
    netloc = userinfo + host + port_text
    path = _normalize_percent(split.path or "/")
    path = _remove_dot_segments(path)
    query = _normalize_query(split.query)
    return urlunsplit((scheme, netloc, path, query, ""))


def deduplicate_urls(urls: Iterable[str]) -> List[str]:
    """Keep the first spelling of each normalized URL."""

    seen = set()
    unique: List[str] = []
    for url in urls:
        key = normalize_cache_key(url)
        if key not in seen:
            seen.add(key)
            unique.append(url)
    return unique


@dataclass
class CacheEntry:
    key: str
    status_code: int
    url: str
    headers: Dict[str, str]
    body: bytes
    canonical_url: Optional[str]
    stored_at: float

    @property
    def etag(self) -> Optional[str]:
        return self.headers.get("etag")

    @property
    def last_modified(self) -> Optional[str]:
        return self.headers.get("last-modified")

    def is_fresh(self, max_age: float, now: Optional[float] = None) -> bool:
        if max_age <= 0:
            return False
        current = time.monotonic() if now is None else now
        return current - self.stored_at <= max_age


class MemoryCache:
    """Thread-safe, process-local cache used by the reference adapter."""

    def __init__(self) -> None:
        self._entries: Dict[str, CacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[CacheEntry]:
        with self._lock:
            return self._entries.get(key)

    def put(self, entry: CacheEntry) -> None:
        with self._lock:
            self._entries[entry.key] = entry

    def delete(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
