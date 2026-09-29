"""Forward only to the configured fixture, with bounded TLS lifetimes."""
import io
import ssl
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.sandbox.http_observer import LoopbackHTTPObserver


class HTTPObserverBoundaryTests(unittest.TestCase):
    def observer(self, url, **kwargs):
        observer = LoopbackHTTPObserver(url, "boundary-test", **kwargs)
        self.addCleanup(observer.close)
        return observer

    def test_localhost_alias_is_pinned_without_dns_resolution(self):
        for host, expected in (("localhost", "127.0.0.1"),
                               ("localhost.localdomain", "127.0.0.1"),
                               ("[::1]", "::1"), ("127.0.0.2", "127.0.0.2")):
            observer = self.observer("http://%s:8080" % host)
            with patch("agent.sandbox.http_observer.http.client.HTTPConnection") as connection:
                observer._upstream_connection()
                connection.assert_called_once_with(expected, 8080, timeout=10)

    def test_foreign_origin_is_rejected(self):
        observer = self.observer("http://127.0.0.1:8080")
        for path in ("http://example.com:8080/", "http://127.0.0.1:8081/"):
            with self.assertRaisesRegex(ValueError, "target-origin-not-allowlisted"):
                observer._request_origin(SimpleNamespace(path=path, headers={}))

    def test_request_host_header_cannot_select_a_different_virtual_host(self):
        observer = self.observer("http://127.0.0.1:8080")
        handler = Mock(path="http://127.0.0.1:8080/fixture", command="GET",
                       headers={"Host": "attacker.example"}, rfile=io.BytesIO(),
                       wfile=io.BytesIO())
        connection = Mock()
        response = Mock(status=200, reason="OK")
        response.getheaders.return_value = []
        response.read.return_value = b""
        connection.getresponse.return_value = response
        with patch.object(observer, "_upstream_connection", return_value=connection):
            observer._proxy_request(handler)
        self.assertEqual("127.0.0.1:8080", connection.request.call_args.kwargs["headers"]["Host"])
        connection.close.assert_called_once()
        self.assertEqual(1, observer.snapshot()["response_count"])

    def test_https_requires_fixture_trust_and_tls12(self):
        observer = self.observer("https://127.0.0.1:8443")
        with self.assertRaisesRegex(ValueError, "certificate-unavailable"):
            observer._upstream_connection()
        observer.tls_certfile = "operator-fixture.pem"
        # Keep a real SSLContext to verify CERT_REQUIRED is never disabled.
        with patch.object(ssl.SSLContext, "load_verify_locations") as trust:
            with patch("agent.sandbox.http_observer.http.client.HTTPSConnection") as connection:
                observer._upstream_connection()
        real_context = connection.call_args.kwargs["context"]
        trust.assert_called_once_with(cafile="operator-fixture.pem")
        self.assertEqual(ssl.CERT_REQUIRED, real_context.verify_mode)
        self.assertEqual(ssl.TLSVersion.TLSv1_2, real_context.minimum_version)
        connection.assert_called_once_with("127.0.0.1", 8443, timeout=10, context=real_context)

    def test_connect_parse_failure_closes_reader_and_tls_socket(self):
        observer = self.observer("https://127.0.0.1:8443",
                                 tls_certfile="fixture.pem", tls_keyfile="fixture.key")
        handler = Mock(path="127.0.0.1:8443")
        reader = io.BytesIO(b"invalid-request\r\n")
        client = Mock()
        client.makefile.return_value = reader
        context = Mock()
        context.wrap_socket.return_value = client
        with patch("agent.sandbox.http_observer.ssl.SSLContext", return_value=context):
            observer._proxy_connect(handler)
        handler.connection.settimeout.assert_called_once_with(10)
        self.assertEqual(ssl.TLSVersion.TLSv1_2, context.minimum_version)
        self.assertTrue(reader.closed)
        client.close.assert_called_once()
        self.assertIn("invalid-https-request-line", observer.snapshot()["observer_gaps"])


if __name__ == "__main__":
    unittest.main()
