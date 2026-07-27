from __future__ import annotations

import unittest

from source_adapter_fuzz import AcquisitionRequest, FixtureServer, HttpAdapter
from source_adapter_fuzz.errors import (
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


class HttpAdapterFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = FixtureServer().start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixtures.stop()

    def setUp(self) -> None:
        self.fixtures.state.reset()
        self.adapter = HttpAdapter()
        self.base = self.fixtures.base_url

    def request(self, path: str, **kwargs: object) -> AcquisitionRequest:
        return AcquisitionRequest(self.base + path, **kwargs)

    def test_ok_json(self) -> None:
        result = self.adapter.acquire(self.request("/ok"))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.content_type, "application/json")
        self.assertIn(b'"fixture"', result.body)
        self.assertEqual(len(result.body_sha256), 64)

    def test_empty_200_is_rejected(self) -> None:
        with self.assertRaises(EmptyResponse):
            self.adapter.acquire(self.request("/empty-200"))

    def test_empty_200_can_be_allowed(self) -> None:
        result = self.adapter.acquire(self.request("/empty-200", allow_empty=True))
        self.assertEqual(result.body, b"")

    def test_finite_redirect_is_followed(self) -> None:
        result = self.adapter.acquire(self.request("/redirect"))
        self.assertEqual(result.url, self.base + "/ok")
        self.assertEqual(result.redirect_chain, (self.base + "/ok",))

    def test_redirect_loop_is_typed(self) -> None:
        with self.assertRaises(RedirectLoop):
            self.adapter.acquire(self.request("/redirect-loop-a"))

    def test_redirect_limit_is_typed(self) -> None:
        adapter = HttpAdapter(max_redirects=0)
        with self.assertRaises(TooManyRedirects):
            adapter.acquire(self.request("/redirect"))

    def test_403_is_terminal(self) -> None:
        with self.assertRaises(Forbidden) as raised:
            self.adapter.acquire(self.request("/forbidden"))
        self.assertTrue(raised.exception.is_terminal)

    def test_429_parses_retry_after(self) -> None:
        with self.assertRaises(RateLimited) as raised:
            self.adapter.acquire(self.request("/rate-limited"))
        self.assertEqual(raised.exception.retry_after, 2.0)
        self.assertTrue(raised.exception.is_continuation)

    def test_500_is_continuation(self) -> None:
        with self.assertRaises(ServerFailure) as raised:
            self.adapter.acquire(self.request("/server-error"))
        self.assertTrue(raised.exception.is_continuation)

    def test_slow_response_times_out(self) -> None:
        with self.assertRaises(ResponseTimeout):
            self.adapter.acquire(self.request("/slow?delay=0.15", timeout=0.03))

    def test_content_type_switches(self) -> None:
        first = self.adapter.acquire(self.request("/content-switch"))
        second = self.adapter.acquire(self.request("/content-switch"))
        self.assertEqual(first.content_type, "text/html")
        self.assertEqual(second.content_type, "application/pdf")

    def test_expected_content_type_mismatch(self) -> None:
        with self.assertRaises(UnexpectedContentType):
            self.adapter.acquire(
                self.request("/ok", expected_content_types=("application/pdf",))
            )

    def test_expected_content_type_wildcard(self) -> None:
        result = self.adapter.acquire(
            self.request("/ok", expected_content_types=("application/*",))
        )
        self.assertEqual(result.content_type, "application/json")

    def test_valid_pdf(self) -> None:
        result = self.adapter.acquire(self.request("/pdf"))
        self.assertTrue(result.body.startswith(b"%PDF-"))

    def test_malformed_pdf_is_rejected(self) -> None:
        with self.assertRaises(MalformedPDF):
            self.adapter.acquire(self.request("/malformed-pdf"))

    def test_pdf_validation_can_be_disabled(self) -> None:
        result = self.adapter.acquire(self.request("/malformed-pdf", validate_pdf=False))
        self.assertEqual(result.body, b"not really a PDF")

    def test_truncated_body_is_rejected(self) -> None:
        with self.assertRaises(TruncatedBody):
            self.adapter.acquire(self.request("/truncated"))

    def test_etag_revalidation_uses_cached_body(self) -> None:
        first = self.adapter.acquire(self.request("/etag"))
        second = self.adapter.acquire(self.request("/etag"))
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(first.body, second.body)
        self.assertEqual(self.fixtures.state.count("/etag"), 2)

    def test_fresh_cache_skips_transport(self) -> None:
        first = self.adapter.acquire(self.request("/etag", cache_max_age=30))
        second = self.adapter.acquire(self.request("/etag", cache_max_age=30))
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(self.fixtures.state.count("/etag"), 1)

    def test_stale_etag_is_rejected(self) -> None:
        self.adapter.acquire(self.request("/stale-etag"))
        with self.assertRaises(StaleCache):
            self.adapter.acquire(self.request("/stale-etag"))

    def test_javascript_empty_shell_is_rejected(self) -> None:
        with self.assertRaises(JavaScriptShell):
            self.adapter.acquire(self.request("/javascript-shell"))

    def test_javascript_shell_detection_can_be_disabled(self) -> None:
        result = self.adapter.acquire(
            self.request("/javascript-shell", detect_javascript_shell=False)
        )
        self.assertIn(b'id="app"', result.body)

    def test_moved_resource_records_canonical(self) -> None:
        result = self.adapter.acquire(self.request("/moved"))
        self.assertEqual(result.canonical_url, self.base + "/canonical")
        self.assertEqual(result.redirect_chain, (self.base + "/canonical",))

    def test_charset_problem_is_typed(self) -> None:
        with self.assertRaises(CharsetFailure):
            self.adapter.acquire(self.request("/charset-problem"))

    def test_latin1_charset_is_accepted(self) -> None:
        result = self.adapter.acquire(self.request("/charset-latin1"))
        self.assertEqual(result.body.decode("latin-1"), "caf\u00e9")

    def test_network_disconnect_is_typed(self) -> None:
        with self.assertRaises(NetworkFailure):
            self.adapter.acquire(self.request("/network-exception"))

    def test_head_does_not_require_body(self) -> None:
        result = self.adapter.acquire(
            AcquisitionRequest(self.base + "/ok", method="HEAD")
        )
        self.assertEqual(result.body, b"")
        self.assertEqual(result.status_code, 200)

    def test_relative_request_is_terminal_client_failure(self) -> None:
        with self.assertRaises(ClientFailure):
            self.adapter.acquire(AcquisitionRequest("/relative"))


if __name__ == "__main__":
    unittest.main()
