"""Adapter protocol and zero-dependency HTTP reference implementation."""

from __future__ import annotations

import email.utils
import http.client
import re
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Mapping, Optional, Protocol, Tuple, Union, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .cache import CacheEntry, MemoryCache, normalize_cache_key
from .errors import (
    CancelledAttempt,
    CharsetFailure,
    ClientFailure,
    EmptyResponse,
    Forbidden,
    JavaScriptShell,
    MalformedPDF,
    NetworkFailure,
    RateLimited,
    RedirectLoop,
    ResponseTimeout,
    ServerFailure,
    StaleCache,
    TooManyRedirects,
    TruncatedBody,
    UnexpectedContentType,
)
from .models import AcquisitionRequest, AdapterResult


@runtime_checkable
class SourceAdapter(Protocol):
    """Minimal public adapter contract.

    Implementations return ``AdapterResult`` or raise a typed
    ``AcquisitionError``. They should inspect ``cancellation`` at safe points.
    """

    name: str

    def acquire(
        self,
        request: AcquisitionRequest,
        cancellation: Optional[threading.Event] = None,
    ) -> AdapterResult:
        ...


ScoreFunction = Callable[[AcquisitionRequest], float]


@dataclass(frozen=True)
class Strategy:
    """An adapter and its ordering score (higher is attempted first)."""

    adapter: SourceAdapter
    score: Union[float, ScoreFunction] = 0.0
    label: Optional[str] = None

    @property
    def name(self) -> str:
        return self.label or self.adapter.name

    def score_for(self, request: AcquisitionRequest) -> float:
        value = self.score(request) if callable(self.score) else self.score
        return float(value)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


_CHARSET = re.compile(r"(?:^|;)\s*charset\s*=\s*[\"']?([^;\"']+)", re.IGNORECASE)
_CANONICAL_LINK = re.compile(
    r"<link\b[^>]*\brel\s*=\s*[\"']canonical[\"'][^>]*\bhref\s*=\s*[\"']([^\"']+)",
    re.IGNORECASE,
)
_CANONICAL_LINK_REVERSED = re.compile(
    r"<link\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"'][^>]*\brel\s*=\s*[\"']canonical[\"']",
    re.IGNORECASE,
)


class HttpAdapter:
    """Small deterministic HTTP adapter built entirely on the standard library."""

    name = "http"

    def __init__(
        self,
        *,
        cache: Optional[MemoryCache] = None,
        max_redirects: int = 5,
        user_agent: str = "source-adapter-fuzz/0.1",
        read_chunk_size: int = 65536,
    ) -> None:
        if max_redirects < 0:
            raise ValueError("max_redirects cannot be negative")
        self.cache = cache if cache is not None else MemoryCache()
        self.max_redirects = max_redirects
        self.user_agent = user_agent
        self.read_chunk_size = read_chunk_size
        self._opener = build_opener(_NoRedirect())

    def acquire(
        self,
        request: AcquisitionRequest,
        cancellation: Optional[threading.Event] = None,
    ) -> AdapterResult:
        started = time.monotonic()
        event = cancellation or threading.Event()
        self._check_cancelled(event)
        split = urlsplit(request.url)
        if split.scheme not in {"http", "https"} or not split.hostname:
            raise ClientFailure("reference HTTP adapter requires an absolute http(s) URL")

        normalized = normalize_cache_key(request.url)
        cache_key = "{} {}".format(request.method, normalized)
        cached = self.cache.get(cache_key) if request.use_cache else None
        if cached is not None and cached.is_fresh(request.cache_max_age):
            return self._cached_result(cached, started)

        outgoing_headers = {str(key): str(value) for key, value in request.headers.items()}
        lower_outgoing = {key.lower() for key in outgoing_headers}
        if "user-agent" not in lower_outgoing:
            outgoing_headers["User-Agent"] = self.user_agent
        if cached is not None:
            if cached.etag and "if-none-match" not in lower_outgoing:
                outgoing_headers["If-None-Match"] = cached.etag
            if cached.last_modified and "if-modified-since" not in lower_outgoing:
                outgoing_headers["If-Modified-Since"] = cached.last_modified

        current_url = request.url
        redirect_chain: List[str] = []
        visited = {normalize_cache_key(current_url)}
        while True:
            self._check_cancelled(event)
            status, final_headers, body = self._single_request(
                current_url,
                request.method,
                outgoing_headers,
                request.timeout,
                event,
            )
            if status in {301, 302, 303, 307, 308}:
                location = final_headers.get("location")
                if not location:
                    raise ClientFailure("redirect response omitted Location", details={"status": status})
                next_url = urljoin(current_url, location)
                try:
                    next_key = normalize_cache_key(next_url)
                except ValueError as exc:
                    raise ClientFailure("redirect Location is not a valid absolute URL") from exc
                redirect_chain.append(next_url)
                if next_key in visited:
                    raise RedirectLoop(
                        "redirect loop detected",
                        details={"chain": tuple(redirect_chain)},
                    )
                if len(redirect_chain) > self.max_redirects:
                    raise TooManyRedirects(
                        "redirect limit exceeded",
                        details={"max_redirects": self.max_redirects, "chain": tuple(redirect_chain)},
                    )
                visited.add(next_key)
                current_url = next_url
                continue
            break

        if status == 304:
            if cached is None:
                raise StaleCache("received 304 without a local cache entry")
            response_etag = final_headers.get("etag")
            stale_marker = final_headers.get("x-source-adapter-stale", "").lower() in {"1", "true", "yes"}
            if stale_marker or (response_etag and cached.etag and response_etag != cached.etag):
                raise StaleCache(
                    "validator response conflicts with cached entity",
                    details={"cached_etag": cached.etag, "response_etag": response_etag},
                )
            return self._cached_result(cached, started, redirect_chain=tuple(redirect_chain))
        if status == 403:
            raise Forbidden("server returned HTTP 403", details={"url": current_url})
        if status == 429:
            retry_after = self._parse_retry_after(final_headers.get("retry-after"))
            raise RateLimited(
                "server returned HTTP 429",
                retry_after=retry_after,
                details={"raw_retry_after": final_headers.get("retry-after")},
            )
        if status >= 500:
            raise ServerFailure("server returned HTTP {}".format(status), details={"status": status})
        if status >= 400:
            raise ClientFailure("server returned HTTP {}".format(status), details={"status": status})
        if status < 200 or status >= 300:
            raise ClientFailure("unsupported HTTP status {}".format(status), details={"status": status})

        self._validate_body(body, final_headers, request)
        canonical = self._canonical_url(current_url, final_headers, body)
        result = AdapterResult(
            status_code=status,
            url=current_url,
            headers=final_headers,
            body=body,
            elapsed_ms=(time.monotonic() - started) * 1000.0,
            adapter=self.name,
            canonical_url=canonical,
            redirect_chain=tuple(redirect_chain),
        )
        if request.use_cache and request.method == "GET" and (final_headers.get("etag") or final_headers.get("last-modified")):
            self.cache.put(
                CacheEntry(
                    key=cache_key,
                    status_code=status,
                    url=current_url,
                    headers=dict(final_headers),
                    body=body,
                    canonical_url=canonical,
                    stored_at=time.monotonic(),
                )
            )
        return result

    def _single_request(
        self,
        url: str,
        method: str,
        headers: Mapping[str, str],
        timeout: float,
        cancellation: threading.Event,
    ) -> Tuple[int, Dict[str, str], bytes]:
        req = Request(url=url, method=method, headers=dict(headers))
        try:
            try:
                response = self._opener.open(req, timeout=timeout)
            except HTTPError as exc:
                response = exc
            with response:
                status = int(response.getcode())
                normalized_headers = {key.lower(): value.strip() for key, value in response.headers.items()}
                if method == "HEAD" or status == 304:
                    return status, normalized_headers, b""
                chunks: List[bytes] = []
                while True:
                    self._check_cancelled(cancellation)
                    chunk = response.read(self.read_chunk_size)
                    if not chunk:
                        break
                    chunks.append(chunk)
                body = b"".join(chunks)
                declared = normalized_headers.get("content-length")
                if declared is not None:
                    try:
                        expected = int(declared)
                    except ValueError:
                        raise TruncatedBody("invalid Content-Length header", details={"content_length": declared})
                    if expected != len(body):
                        raise TruncatedBody(
                            "response body length differs from Content-Length",
                            details={"expected": expected, "actual": len(body)},
                        )
                return status, normalized_headers, body
        except http.client.IncompleteRead as exc:
            raise TruncatedBody(
                "connection closed before declared body completed",
                details={"received": len(exc.partial)},
            ) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise ResponseTimeout("response exceeded {:.3f}s timeout".format(timeout)) from exc
        except URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise ResponseTimeout("response exceeded {:.3f}s timeout".format(timeout)) from exc
            raise NetworkFailure("network request failed: {}".format(exc.reason)) from exc
        except (ConnectionError, OSError, http.client.HTTPException) as exc:
            raise NetworkFailure("network request failed: {}".format(exc)) from exc

    def _validate_body(
        self,
        body: bytes,
        headers: Mapping[str, str],
        request: AcquisitionRequest,
    ) -> None:
        if request.method == "HEAD":
            return
        if not body and not request.allow_empty:
            raise EmptyResponse("HTTP 200 response contained no body")
        content_type_header = headers.get("content-type", "")
        media_type = content_type_header.split(";", 1)[0].strip().lower()
        if request.expected_content_types and not self._content_type_matches(media_type, request.expected_content_types):
            raise UnexpectedContentType(
                "expected {}, received {}".format(
                    ", ".join(request.expected_content_types), media_type or "<missing>"
                ),
                details={"expected": request.expected_content_types, "actual": media_type},
            )
        if media_type == "application/pdf" and request.validate_pdf and body:
            if not body.startswith(b"%PDF-") or b"%%EOF" not in body[-1024:]:
                raise MalformedPDF("PDF signature or EOF marker is missing")
        if self._is_textual(media_type) and body:
            text = self._decode_text(body, content_type_header)
            if request.detect_javascript_shell and self._looks_like_javascript_shell(text):
                raise JavaScriptShell("HTML is a JavaScript-only empty shell")

    @staticmethod
    def _content_type_matches(media_type: str, expected: Tuple[str, ...]) -> bool:
        for item in expected:
            candidate = item.lower().split(";", 1)[0].strip()
            if candidate == media_type or (candidate.endswith("/*") and media_type.startswith(candidate[:-1])):
                return True
        return False

    @staticmethod
    def _is_textual(media_type: str) -> bool:
        return media_type.startswith("text/") or media_type in {
            "application/json",
            "application/javascript",
            "application/xml",
            "application/xhtml+xml",
        }

    @staticmethod
    def _decode_text(body: bytes, content_type_header: str) -> str:
        match = _CHARSET.search(content_type_header)
        charset = match.group(1).strip() if match else "utf-8"
        try:
            return body.decode(charset, errors="strict")
        except (LookupError, UnicodeDecodeError) as exc:
            raise CharsetFailure(
                "body cannot be decoded with declared charset {}".format(charset),
                details={"charset": charset},
            ) from exc

    @staticmethod
    def _looks_like_javascript_shell(text: str) -> bool:
        lower = text.lower()
        if "data-source-adapter-fuzz-shell" in lower:
            return True
        has_mount = bool(re.search(r"<(?:div|main)\b[^>]*\bid=[\"'](?:app|root)[\"'][^>]*>\s*</", lower))
        has_script = "<script" in lower
        visible = re.sub(r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>", "", lower, flags=re.DOTALL)
        visible = re.sub(r"<[^>]+>", " ", visible)
        visible = re.sub(r"\s+", "", visible)
        return has_mount and has_script and len(visible) < 24

    @staticmethod
    def _canonical_url(current_url: str, headers: Mapping[str, str], body: bytes) -> str:
        content_location = headers.get("content-location")
        if content_location:
            return urljoin(current_url, content_location)
        content_type = headers.get("content-type", "").lower()
        if "html" in content_type and body:
            text = body.decode("latin-1", errors="ignore")
            match = _CANONICAL_LINK.search(text) or _CANONICAL_LINK_REVERSED.search(text)
            if match:
                return urljoin(current_url, match.group(1))
        return current_url

    @staticmethod
    def _parse_retry_after(value: Optional[str]) -> Optional[float]:
        if not value:
            return None
        try:
            return max(0.0, float(value.strip()))
        except ValueError:
            try:
                parsed = email.utils.parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    @staticmethod
    def _check_cancelled(cancellation: threading.Event) -> None:
        if cancellation.is_set():
            raise CancelledAttempt("attempt cancelled because another strategy completed")

    def _cached_result(
        self,
        entry: CacheEntry,
        started: float,
        *,
        redirect_chain: Tuple[str, ...] = (),
    ) -> AdapterResult:
        return AdapterResult(
            status_code=entry.status_code,
            url=entry.url,
            headers=dict(entry.headers),
            body=entry.body,
            elapsed_ms=(time.monotonic() - started) * 1000.0,
            adapter=self.name,
            from_cache=True,
            canonical_url=entry.canonical_url,
            redirect_chain=redirect_chain,
        )
