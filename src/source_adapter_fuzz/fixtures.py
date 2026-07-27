"""Deterministic localhost fault-injection HTTP fixture server."""

from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit


VALID_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


@dataclass
class FixtureState:
    counts: Dict[str, int] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def hit(self, path: str) -> int:
        with self.lock:
            value = self.counts.get(path, 0) + 1
            self.counts[path] = value
            return value

    def count(self, path: str) -> int:
        with self.lock:
            return self.counts.get(path, 0)

    def reset(self) -> None:
        with self.lock:
            self.counts.clear()


class FixtureHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: Tuple[str, int], state: Optional[FixtureState] = None) -> None:
        self.state = state or FixtureState()
        super().__init__(server_address, FixtureRequestHandler)

    def handle_error(self, request, client_address):  # type: ignore[no-untyped-def]
        # Timeout and truncation scenarios intentionally make peers disconnect.
        return


class FixtureRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SourceAdapterFuzzFixture/0.1"

    @property
    def fixture_server(self) -> FixtureHTTPServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_HEAD(self) -> None:
        self._dispatch(head_only=True)

    def do_GET(self) -> None:
        self._dispatch(head_only=False)

    def _dispatch(self, *, head_only: bool) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        hit = self.fixture_server.state.hit(path)

        if path == "/ok":
            self._send(200, b'{"status":"ok","source":"fixture"}', "application/json", head_only=head_only)
        elif path == "/empty-200":
            self._send(200, b"", "text/plain; charset=utf-8", head_only=head_only)
        elif path == "/redirect":
            self._send(302, b"", headers={"Location": "/ok"}, head_only=head_only)
        elif path == "/redirect-loop-a":
            self._send(302, b"", headers={"Location": "/redirect-loop-b"}, head_only=head_only)
        elif path == "/redirect-loop-b":
            self._send(302, b"", headers={"Location": "/redirect-loop-a"}, head_only=head_only)
        elif path == "/forbidden":
            self._send(403, b"forbidden", "text/plain; charset=utf-8", head_only=head_only)
        elif path == "/rate-limited":
            self._send(
                429,
                b"rate limited",
                "text/plain; charset=utf-8",
                headers={"Retry-After": "2"},
                head_only=head_only,
            )
        elif path == "/server-error":
            self._send(500, b"fixture server error", "text/plain; charset=utf-8", head_only=head_only)
        elif path == "/slow":
            try:
                delay = min(max(float(query.get("delay", ["0.25"])[0]), 0.0), 2.0)
            except ValueError:
                delay = 0.25
            time.sleep(delay)
            self._send(200, b"slow but complete", "text/plain; charset=utf-8", head_only=head_only)
        elif path == "/content-switch":
            if hit % 2 == 1:
                self._send(
                    200,
                    b"<!doctype html><title>HTML phase</title><p>first response</p>",
                    "text/html; charset=utf-8",
                    headers={"X-Fixture-Phase": "html"},
                    head_only=head_only,
                )
            else:
                self._send(
                    200,
                    VALID_PDF,
                    "application/pdf",
                    headers={"X-Fixture-Phase": "pdf"},
                    head_only=head_only,
                )
        elif path == "/pdf":
            self._send(200, VALID_PDF, "application/pdf", head_only=head_only)
        elif path == "/malformed-pdf":
            self._send(200, b"not really a PDF", "application/pdf", head_only=head_only)
        elif path == "/truncated":
            body = b"short"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", "100")
            self.send_header("Connection", "close")
            self.end_headers()
            if not head_only:
                try:
                    self.wfile.write(body)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            self.close_connection = True
        elif path == "/etag":
            etag = '"fixture-v1"'
            if self.headers.get("If-None-Match") == etag:
                self._send(304, b"", headers={"ETag": etag}, head_only=head_only)
            else:
                self._send(
                    200,
                    b"stable cached representation",
                    "text/plain; charset=utf-8",
                    headers={"ETag": etag, "Cache-Control": "max-age=0"},
                    head_only=head_only,
                )
        elif path == "/stale-etag":
            old_etag = '"fixture-old"'
            if self.headers.get("If-None-Match"):
                self._send(
                    304,
                    b"",
                    headers={"ETag": '"fixture-new"', "X-Source-Adapter-Stale": "true"},
                    head_only=head_only,
                )
            else:
                self._send(
                    200,
                    b"old cached representation",
                    "text/plain; charset=utf-8",
                    headers={"ETag": old_etag, "Cache-Control": "max-age=0"},
                    head_only=head_only,
                )
        elif path == "/javascript-shell":
            shell = (
                b'<!doctype html><html><body><div id="app"></div>'
                b'<script src="bundle.js"></script></body></html>'
            )
            self._send(
                200,
                shell,
                "text/html; charset=utf-8",
                headers={"X-Fixture-Marker": "data-source-adapter-fuzz-shell"},
                head_only=head_only,
            )
        elif path == "/javascript-shell-marker":
            shell = b'<html data-source-adapter-fuzz-shell="true"><body></body></html>'
            self._send(200, shell, "text/html; charset=utf-8", head_only=head_only)
        elif path == "/duplicate-urls":
            base = self._base_url()
            payload = json.dumps(
                {
                    "urls": [
                        base + "/ok?b=2&a=1",
                        base + "/ok?a=1&b=2#display-only",
                        base + "/ok?a=1&b=3",
                    ]
                },
                sort_keys=True,
            ).encode("utf-8")
            self._send(200, payload, "application/json", head_only=head_only)
        elif path == "/moved":
            self._send(301, b"", headers={"Location": "/canonical"}, head_only=head_only)
        elif path == "/canonical":
            canonical = self._base_url() + "/canonical"
            body = (
                '<!doctype html><head><link rel="canonical" href="{}"></head>'
                "<body>canonical public record</body>"
            ).format(canonical).encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8", head_only=head_only)
        elif path == "/charset-problem":
            self._send(200, b"invalid utf-8: \xff\xfe", "text/plain; charset=utf-8", head_only=head_only)
        elif path == "/charset-latin1":
            self._send(200, "caf\u00e9".encode("latin-1"), "text/plain; charset=iso-8859-1", head_only=head_only)
        elif path == "/network-exception":
            # Close before an HTTP status line. The client observes RemoteDisconnected.
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            self.close_connection = True
        elif path == "/partial-batch":
            base = self._base_url()
            payload = json.dumps(
                {"urls": [base + "/ok", base + "/server-error", base + "/pdf"]},
                sort_keys=True,
            ).encode("utf-8")
            self._send(200, payload, "application/json", head_only=head_only)
        elif path == "/health":
            self._send(200, b"healthy", "text/plain; charset=utf-8", head_only=head_only)
        else:
            self._send(404, b"unknown fixture", "text/plain; charset=utf-8", head_only=head_only)

    def _base_url(self) -> str:
        host, port = self.fixture_server.server_address[:2]
        display_host = "127.0.0.1" if host in {"", "0.0.0.0", "::"} else host
        if ":" in display_host and not display_host.startswith("["):
            display_host = "[{}]".format(display_host)
        return "http://{}:{}".format(display_host, port)

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: Optional[str] = None,
        *,
        headers: Optional[Dict[str, str]] = None,
        head_only: bool,
    ) -> None:
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only and body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass


class FixtureServer(AbstractContextManager):
    """Context manager that runs fixtures on an ephemeral localhost port."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("fixture server is localhost-only")
        self.server = FixtureHTTPServer((host, port))
        self.thread: Optional[threading.Thread] = None

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        if host == "::1":
            host = "[::1]"
        return "http://{}:{}".format(host, port)

    @property
    def state(self) -> FixtureState:
        return self.server.state

    def start(self) -> "FixtureServer":
        if self.thread is None:
            self.thread = threading.Thread(
                target=self.server.serve_forever,
                name="source-adapter-fuzz-fixtures",
                daemon=True,
            )
            self.thread.start()
        return self

    def stop(self) -> None:
        if self.thread is not None:
            self.server.shutdown()
            self.thread.join(timeout=2.0)
            self.thread = None
        self.server.server_close()

    def __enter__(self) -> "FixtureServer":
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[no-untyped-def]
        self.stop()
