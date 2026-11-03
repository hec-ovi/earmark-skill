"""Local-only hardening: Host allowlist, X-Earmark, Content-Type, body limit, headers, no CORS."""

from __future__ import annotations

import socket
import unittest

from tests.test_server_fixtures import FakeServerCase  # first: puts src/ on sys.path

from earmark.server import app  # noqa: E402


class HostTests(FakeServerCase):
    def test_allowed_hosts(self):
        port = self.h.port
        for host in (f"127.0.0.1:{port}", f"localhost:{port}", f"LOCALHOST:{port}"):
            with self.subTest(host=host):
                self.assertEnvelopeOk(self.call("GET", "/api/board", host=host))

    def test_rejected_hosts(self):
        port = self.h.port
        for host in ("evil.example", f"evil.example:{port}", "127.0.0.1", f"127.0.0.1:{port + 1}",
                     f"localhost.evil.example:{port}", f"[::1]:{port}", ""):
            with self.subTest(host=host):
                resp = self.call("GET", "/api/board", host=host)
                self.assertEnvelopeError(resp, 403, "forbidden")
        self.assertEqual(self.store.names(), [])  # nothing reached the board

    def test_rejected_host_blocks_static_and_writes(self):
        bad = f"rebind.example:{self.h.port}"
        self.assertEnvelopeError(self.call("GET", "/", host=bad), 403, "forbidden")
        self.assertEnvelopeError(self.call("POST", "/api/issues", {"title": "x"}, host=bad), 403, "forbidden")
        self.assertNotIn("create_issue", self.store.names())

    def test_missing_host_header(self):
        sock = socket.create_connection(("127.0.0.1", self.h.port), timeout=5)
        try:
            sock.sendall(b"GET /api/board HTTP/1.0\r\n\r\n")
            data = b""
            while chunk := sock.recv(4096):
                data += chunk
        finally:
            sock.close()
        self.assertTrue(data.startswith(b"HTTP/1.1 403"), data[:80])

    def test_allowed_host_sets(self):
        self.assertEqual(app._allowed_hosts("127.0.0.1", 7477), {"127.0.0.1:7477", "localhost:7477"})
        self.assertEqual(app._allowed_hosts("::1", 7477),
                         {"127.0.0.1:7477", "localhost:7477", "[::1]:7477"})
        self.assertIn("192.168.1.20:7477", app._allowed_hosts("192.168.1.20", 7477))
        self.assertIn("localhost:80", app._allowed_hosts("127.0.0.1", 80))
        self.assertIn("localhost", app._allowed_hosts("127.0.0.1", 80))


class WriteGuardTests(FakeServerCase):
    def test_missing_x_earmark(self):
        for method, path, body in (("POST", "/api/issues", {"title": "x"}),
                                   ("PATCH", "/api/issues/GAME-1", {"version": 1, "title": "x"}),
                                   ("DELETE", "/api/issues/GAME-1", None)):
            with self.subTest(method=method):
                resp = self.call(method, path, body, headers={"X-Earmark": None})
                err = self.assertEnvelopeError(resp, 403, "forbidden")
                self.assertIn("X-Earmark", err["message"])
        self.assertEqual(self.store.names(), [])

    def test_wrong_x_earmark_value(self):
        resp = self.call("POST", "/api/issues", {"title": "x"}, headers={"X-Earmark": "yes"})
        self.assertEnvelopeError(resp, 403, "forbidden")

    def test_content_type_must_be_json(self):
        for ctype in (None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
            with self.subTest(ctype=ctype):
                resp = self.call("POST", "/api/issues", {"title": "x"}, headers={"Content-Type": ctype})
                self.assertEnvelopeError(resp, 400, "invalid_request")
        resp = self.call("POST", "/api/issues", {"title": "x"},
                         headers={"Content-Type": "application/json; charset=utf-8"})
        self.assertEnvelopeOk(resp)

    def test_bodyless_delete_is_accepted(self):
        self.assertEnvelopeOk(self.call("DELETE", "/api/issues/GAME-1"))

    def test_cross_origin_refused(self):
        resp = self.call("POST", "/api/issues", {"title": "x"}, headers={"Origin": "https://evil.example"})
        self.assertEnvelopeError(resp, 403, "forbidden")
        resp = self.call("POST", "/api/issues", {"title": "x"}, headers={"Origin": "null"})
        self.assertEnvelopeError(resp, 403, "forbidden")
        resp = self.call("POST", "/api/issues", {"title": "x"},
                         headers={"Origin": f"http://127.0.0.1:{self.h.port}"})
        self.assertEnvelopeOk(resp)

    def test_oversize_body(self):
        big = b'{"title": "' + b"x" * (1024 * 1024) + b'"}'
        resp = self.call("POST", "/api/issues", raw=big)
        self.assertEnvelopeError(resp, 413, "invalid_request")
        self.assertEqual(resp.headers["Connection"], "close")
        self.assertNotIn("create_issue", self.store.names())

    def test_body_at_limit_is_read(self):
        body = b'{"title": "x", "body": "' + b"y" * (1024 * 1024 - 40) + b'"}'
        self.assertLessEqual(len(body), 1024 * 1024)
        self.assertEnvelopeOk(self.call("POST", "/api/issues", raw=body))
        self.assertEqual(len(self.store.last("create_issue")[1]["body"]), 1024 * 1024 - 40)

    def test_invalid_json(self):
        for raw in (b"{", b"\xff\xfe", b'{"title": NaN}', b"[1,"):
            with self.subTest(raw=raw):
                self.assertEnvelopeError(self.call("POST", "/api/issues", raw=raw), 400, "invalid_request")

    def test_bad_content_length(self):
        resp = self.call("POST", "/api/issues", raw=b"{}", headers={"Content-Length": "abc"})
        self.assertEnvelopeError(resp, 400, "invalid_request")

    def test_chunked_refused(self):
        resp = self.call("POST", "/api/issues", raw=b"", headers={"Transfer-Encoding": "chunked",
                                                                  "Content-Length": None})
        self.assertEnvelopeError(resp, 400, "invalid_request")

    def test_options_preflight_gets_no_cors(self):
        resp = self.call("OPTIONS", "/api/issues", headers={
            "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-earmark, content-type"}, write_headers=False)
        self.assertEqual(resp.status, 405)
        self.assertFalse([h for h in resp.headers if h.lower().startswith("access-control-")])


class HeaderTests(FakeServerCase):
    def test_no_cors_headers_anywhere(self):
        for method, path in (("GET", "/"), ("GET", "/api/board"), ("POST", "/api/issues"),
                             ("GET", "/api/nope")):
            resp = self.call(method, path, {"title": "x"} if method == "POST" else None,
                             headers={"Origin": f"http://127.0.0.1:{self.h.port}"})
            self.assertFalse([h for h in resp.headers if h.lower().startswith("access-control-")], path)

    def test_common_headers(self):
        for path in ("/", "/api/board", "/api/nope"):
            resp = self.call("GET", path)
            self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(resp.headers["Referrer-Policy"], "no-referrer")
            self.assertTrue(resp.headers["Server"].startswith("earmark/"))
            self.assertNotIn("Python", resp.headers["Server"])

    def test_csp_on_html_only(self):
        resp = self.call("GET", "/")
        self.assertEqual(resp.headers["Content-Security-Policy"], app.CSP)
        self.assertIn("frame-ancestors 'none'", app.CSP)
        self.assertIn("form-action 'none'", app.CSP)
        self.assertEqual(resp.headers["X-Frame-Options"], "DENY")
        self.assertIsNone(self.call("GET", "/api/board").headers["Content-Security-Policy"])

    def test_exact_csp(self):
        self.assertEqual(app.CSP, "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                                  "script-src 'self'; connect-src 'self'; font-src 'self'; "
                                  "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")

    def test_malformed_request_line_gets_json(self):
        sock = socket.create_connection(("127.0.0.1", self.h.port), timeout=5)
        try:
            sock.sendall(b"BREW /pot HTTP/1.1\r\nHost: x\r\n\r\n")
            data = b""
            while chunk := sock.recv(4096):
                data += chunk
        finally:
            sock.close()
        head, _, body = data.partition(b"\r\n\r\n")
        self.assertIn(b"501", head.split(b"\r\n")[0])
        self.assertIn(b'"ok":false', body)
        self.assertNotIn(b"<html", body.lower())


if __name__ == "__main__":
    unittest.main()
