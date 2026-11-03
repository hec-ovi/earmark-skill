"""Static UI files: index, assets, content types, ETag revalidation, traversal, placeholder."""

from __future__ import annotations

import os
import shutil
import socket
import tempfile
import unittest

from tests.test_server_fixtures import FakeServerCase  # first: puts src/ on sys.path

from earmark.server.static import StaticFiles, default_ui_dir  # noqa: E402


def _make_ui() -> str:
    root = tempfile.mkdtemp(prefix="earmark-ui-test-")
    files = {
        "index.html": b"<!doctype html><title>t</title><script type=module src=/assets/js/app.js></script>",
        "js/app.js": b"export const x = 1;\n",
        "css/app.css": b"body{margin:0}\n",
        "icons.svg": b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        "data.json": b"{}",
        "img/logo.png": b"\x89PNG\r\n\x1a\nfake",
        "fonts/inter.woff2": b"wOF2fake",
        ".secret": b"hidden",
        "blob.bin": b"\x00\x01",
    }
    for rel, data in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    return root


class StaticServingTests(FakeServerCase):
    @classmethod
    def setUpClass(cls):
        cls.ui_dir = _make_ui()
        cls.outside = tempfile.mkdtemp(prefix="earmark-outside-")
        with open(os.path.join(cls.outside, "secret.txt"), "w") as f:
            f.write("outside")
        try:
            os.symlink(cls.outside, os.path.join(cls.ui_dir, "escape"))
        except (OSError, NotImplementedError):
            pass

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.ui_dir, ignore_errors=True)
        shutil.rmtree(cls.outside, ignore_errors=True)

    def test_index(self):
        resp = self.call("GET", "/")
        self.assertEqual(resp.status, 200)
        self.assertTrue(resp.body.startswith(b"<!doctype html>"))
        self.assertEqual(resp.headers["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(resp.headers["Cache-Control"], "no-cache")
        self.assertTrue(resp.headers["ETag"].startswith('"'))
        self.assertIn("default-src 'self'", resp.headers["Content-Security-Policy"])

    def test_content_types(self):
        cases = {
            "/assets/js/app.js": "text/javascript; charset=utf-8",
            "/assets/css/app.css": "text/css; charset=utf-8",
            "/assets/icons.svg": "image/svg+xml",
            "/assets/data.json": "application/json; charset=utf-8",
            "/assets/img/logo.png": "image/png",
            "/assets/fonts/inter.woff2": "font/woff2",
            "/assets/blob.bin": "application/octet-stream",
            "/assets/index.html": "text/html; charset=utf-8",
        }
        for path, ctype in cases.items():
            with self.subTest(path=path):
                resp = self.call("GET", path)
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers["Content-Type"], ctype)
                self.assertEqual(resp.headers["Cache-Control"], "no-cache")
                self.assertTrue(resp.headers["ETag"])
                self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")

    def test_svg_gets_csp(self):
        self.assertTrue(self.call("GET", "/assets/icons.svg").headers["Content-Security-Policy"])
        self.assertIsNone(self.call("GET", "/assets/js/app.js").headers["Content-Security-Policy"])

    def test_etag_revalidation(self):
        first = self.call("GET", "/assets/js/app.js")
        etag = first.headers["ETag"]
        again = self.call("GET", "/assets/js/app.js", headers={"If-None-Match": etag})
        self.assertEqual(again.status, 304)
        self.assertEqual(again.body, b"")
        self.assertEqual(again.headers["ETag"], etag)
        other = self.call("GET", "/assets/js/app.js", headers={"If-None-Match": '"nope", ' + etag})
        self.assertEqual(other.status, 304)
        stale = self.call("GET", "/assets/js/app.js", headers={"If-None-Match": '"nope"'})
        self.assertEqual(stale.status, 200)

    def test_etag_changes_with_content(self):
        path = os.path.join(self.ui_dir, "css", "live.css")
        with open(path, "wb") as f:
            f.write(b"a{}")
        first = self.call("GET", "/assets/css/live.css").headers["ETag"]
        with open(path, "wb") as f:
            f.write(b"a{color:red}")
        second = self.call("GET", "/assets/css/live.css")
        self.assertEqual(second.body, b"a{color:red}")
        self.assertNotEqual(second.headers["ETag"], first)

    def test_head(self):
        resp = self.call("HEAD", "/assets/js/app.js")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.body, b"")
        self.assertEqual(resp.headers["Content-Length"], str(len(b"export const x = 1;\n")))

    def test_missing_and_unrouted(self):
        for path in ("/assets/nope.js", "/assets/", "/assets/js", "/nope", "/js/app.js", "/favicon.ico"):
            with self.subTest(path=path):
                self.assertEnvelopeError(self.call("GET", path), 404, "not_found")

    def test_traversal_blocked(self):
        for path in ("/assets/../index.html", "/assets/%2e%2e/%2e%2e/etc/passwd", "/assets/js/../../x",
                     "/assets/..%2f..%2fetc%2fpasswd", "/assets/%2e%2e%5c%2e%2e%5cwin.ini",
                     "/assets/.secret", "/assets/js/./app.js", "/assets//etc/passwd",
                     "/assets/escape/secret.txt", "/assets/%00", "/assets/%ff%fe"):
            with self.subTest(path=path):
                resp = self.call("GET", path)
                self.assertEqual(resp.status, 404, resp.body)
                self.assertNotIn(b"outside", resp.body)
                self.assertNotIn(b"root:", resp.body)

    def test_raw_dotdot_request_target(self):
        sock = socket.create_connection(("127.0.0.1", self.h.port), timeout=5)
        try:
            sock.sendall(f"GET /assets/../../../../etc/passwd HTTP/1.1\r\nHost: {self.h.host}\r\n"
                         "Connection: close\r\n\r\n".encode())
            data = b""
            while chunk := sock.recv(4096):
                data += chunk
        finally:
            sock.close()
        self.assertIn(b" 404 ", data.split(b"\r\n")[0])

    def test_write_methods_on_static(self):
        resp = self.call("POST", "/assets/js/app.js", {})
        self.assertEnvelopeError(resp, 405, "invalid_request")


class PlaceholderTests(FakeServerCase):
    @classmethod
    def setUpClass(cls):
        cls.ui_dir = tempfile.mkdtemp(prefix="earmark-empty-ui-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.ui_dir, ignore_errors=True)

    def test_placeholder_when_index_missing(self):
        resp = self.call("GET", "/")
        self.assertEqual(resp.status, 200)
        self.assertIn(b"/api/board", resp.body)
        self.assertNotIn(b"<script", resp.body)
        self.assertNotIn(b"style=", resp.body)
        self.assertEqual(resp.headers["Content-Type"], "text/html; charset=utf-8")
        self.assertTrue(resp.headers["Content-Security-Policy"])
        self.assertEqual(self.call("GET", "/", headers={"If-None-Match": resp.headers["ETag"]}).status, 304)


class ResolverTests(unittest.TestCase):
    def test_default_ui_dir_is_the_package_ui(self):
        root = default_ui_dir()
        self.assertEqual(root.name, "ui")
        self.assertEqual(root.parent.name, "earmark")

    def test_resolver_rejects_outside_paths(self):
        root = tempfile.mkdtemp(prefix="earmark-ui-")
        self.addCleanup(shutil.rmtree, root, True)
        with open(os.path.join(root, "a.js"), "w") as f:
            f.write("1")
        files = StaticFiles(root)
        self.assertIsNotNone(files.resolve("a.js"))
        for rel in ("", "../a.js", "/etc/passwd", "a.js/", "x\\..\\a.js", ".hidden", "a\x00.js"):
            self.assertIsNone(files.resolve(rel), rel)


if __name__ == "__main__":
    unittest.main()
