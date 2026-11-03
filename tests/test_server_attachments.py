"""Attachment routes over a real board: streamed upload (413 before the body, temp file while
streaming), list, get, raw bytes (Range/206/416, ETag/304, inline or download, sandbox CSP),
delete, and the fields attachments add to issues and comments."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import tempfile
import time
import unittest

from tests.test_server_fixtures import ServerHarness, core_ready  # first: src/ on sys.path
from tests.test_server_security import raw_exchange

CORE = core_ready()
if CORE:
    from tests.support import join_agent, make_board

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
SANDBOX = "sandbox; default-src 'none'"


@unittest.skipUnless(CORE, "earmark.board is not implemented yet")
class AttachmentRouteCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="earmark-server-files-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        board = make_board(self.tmp)
        self.db = board.path
        self.files = board.files_dir
        board.create_issue("Eye color widget resets on reload")
        board.create_issue("Other")
        board.close()
        self.h = ServerHarness(self.db, poll_interval=0.2)
        self.addCleanup(self.h.stop)

    def upload(self, data: bytes, name: str = "shot.png", ctype: str = "image/png",
               key: str = "GAME-1", query: str = "", **headers):
        from urllib.parse import quote

        path = f"/api/issues/{key}/attachments?name={quote(name)}{query}"
        return self.h.request("POST", path, raw=data, headers={"Content-Type": ctype, **headers})

    def ok(self, resp, status: int = 200):
        self.assertEqual(resp.status, status, resp.body)
        env = resp.json
        self.assertTrue(env["ok"], env)
        return env["data"]

    def err(self, resp, status: int, code: str) -> dict:
        self.assertEqual(resp.status, status, resp.body)
        env = resp.json
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], code, env["error"])
        return env["error"]


class UploadTests(AttachmentRouteCase):
    def test_upload_shape_and_issue_fields(self):
        att = self.ok(self.upload(PNG, "eye-picker-bug.png"))
        sha = hashlib.sha256(PNG).hexdigest()
        self.assertEqual({k: att[k] for k in ("id", "issue", "comment_id", "name", "mime", "kind",
                                               "size", "sha256", "author", "url")},
                         {"id": 1, "issue": "GAME-1", "comment_id": None, "name": "eye-picker-bug.png",
                          "mime": "image/png", "kind": "image", "size": len(PNG), "sha256": sha,
                          "author": "owner", "url": "/api/attachments/1/raw"})
        self.assertEqual(att["path"], os.path.join(self.files, sha[:2], sha + ".png"))
        with open(att["path"], "rb") as fh:
            self.assertEqual(fh.read(), PNG)
        issue = self.ok(self.h.request("GET", "/api/issues/GAME-1"))
        self.assertEqual((issue["attachment_count"], [a["id"] for a in issue["attachments"]]), (1, [1]))
        items = self.ok(self.h.request("GET", "/api/issues"))["items"]
        self.assertEqual({i["key"]: i["attachment_count"] for i in items}, {"GAME-1": 1, "GAME-2": 0})
        self.assertEqual(self.ok(self.h.request("GET", "/api/issues/GAME-1/attachments")), [att])
        self.assertEqual(self.ok(self.h.request("GET", "/api/attachments/1")), att)
        events = self.ok(self.h.request("GET", "/api/issues/GAME-1/events"))
        self.assertEqual(events[-1]["action"], "attach")

    def test_comment_link_and_type_hint(self):
        comment = self.ok(self.h.request("POST", "/api/issues/GAME-1/comments", {"body": "see clip"}))
        att = self.ok(self.upload(b"RIFF....WAVE", "pasted-file", "audio/wav",
                                  query=f"&comment={comment['id']}"))
        self.assertEqual((att["comment_id"], att["kind"], att["mime"]), (comment["id"], "audio", "audio/wav"))
        page = self.ok(self.h.request("GET", "/api/issues/GAME-1/comments"))
        self.assertEqual([a["id"] for a in page["items"][0]["attachments"]], [att["id"]])
        self.err(self.upload(b"x", "a.txt", "text/plain", key="GAME-2", query=f"&comment={comment['id']}"),
                 400, "invalid_request")
        self.err(self.upload(b"x", "a.txt", "text/plain", query="&comment=999"), 404, "not_found")

    def test_bad_uploads(self):
        self.err(self.upload(PNG, key="GAME-9"), 404, "not_found")
        self.err(self.h.request("POST", "/api/issues/GAME-1/attachments", raw=PNG,
                                headers={"Content-Type": "image/png"}), 400, "invalid_request")  # no name
        self.err(self.upload(PNG, **{"X-Earmark": None}), 403, "forbidden")
        self.err(self.upload(PNG, Origin="https://evil.example"), 403, "forbidden")
        self.err(self.upload(b"", "", "text/plain"), 400, "invalid_request")
        resp = self.h.request("POST", "/api/issues/GAME-1/attachments?name=a.txt", raw=b"",
                              headers={"Transfer-Encoding": "chunked", "Content-Length": None})
        self.err(resp, 400, "invalid_request")
        empty = self.ok(self.upload(b"", "empty.txt", "text/plain"))
        self.assertEqual(empty["size"], 0)
        self.assertEqual(sorted(os.listdir(self.files)), [empty["sha256"][:2]])  # no temp left

    def test_large_upload_streams_past_the_json_limit(self):
        data = os.urandom(3 * 1024 * 1024 + 17)
        att = self.ok(self.upload(data, "capture.mp4", "video/mp4"))
        self.assertEqual((att["size"], att["kind"]), (len(data), "video"))
        self.assertEqual(att["sha256"], hashlib.sha256(data).hexdigest())

    def test_body_goes_to_a_temp_file_while_it_arrives(self):
        data = os.urandom(4 * 1024 * 1024)
        head = (f"POST /api/issues/GAME-1/attachments?name=big.bin HTTP/1.1\r\nHost: {self.h.host}\r\n"
                f"X-Earmark: 1\r\nContent-Type: application/octet-stream\r\n"
                f"Content-Length: {len(data)}\r\n\r\n").encode()
        sock = socket.create_connection(("127.0.0.1", self.h.port), timeout=10)
        try:
            sock.sendall(head + data[:2 * 1024 * 1024])
            deadline = time.monotonic() + 10
            grown = 0
            while time.monotonic() < deadline and grown < 1024 * 1024:
                temps = [n for n in os.listdir(self.files) if n.startswith(".upload-")]
                grown = max([os.path.getsize(os.path.join(self.files, n)) for n in temps] or [0])
                time.sleep(0.02)
            self.assertGreaterEqual(grown, 1024 * 1024, "the upload is not written while it streams")
            sock.sendall(data[2 * 1024 * 1024:])
            reply = b""
            while b"\r\n\r\n" not in reply or not reply.rstrip().endswith(b"}"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                reply += chunk
        finally:
            sock.close()
        self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply[:100])
        self.assertEqual([n for n in os.listdir(self.files) if n.startswith(".upload-")], [])

    def test_over_the_limit_is_413_before_the_body(self):
        board = self._board()
        board.config("max_attachment_mb", "1")
        board.close()
        size = 1024 * 1024 + 1
        head = (f"POST /api/issues/GAME-1/attachments?name=big.bin HTTP/1.1\r\nHost: {self.h.host}\r\n"
                f"X-Earmark: 1\r\nContent-Type: application/octet-stream\r\n"
                f"Content-Length: {size}\r\n\r\n").encode()
        sock = socket.create_connection(("127.0.0.1", self.h.port), timeout=10)
        try:
            sock.sendall(head)  # no body yet: the answer must not wait for it
            reply = b""
            while b"\r\n\r\n" not in reply:
                reply += sock.recv(65536)
        finally:
            sock.close()
        self.assertTrue(reply.startswith(b"HTTP/1.1 413"), reply[:100])
        status, headers, body = raw_exchange(self.h.port, head + b"\0" * size)
        self.assertEqual((status, headers["connection"]), (413, "close"))
        err = json.loads(body)["error"]
        self.assertEqual(err["code"], "invalid_request")
        self.assertIn("1 MB attachment limit", err["message"])
        self.assertEqual(err["details"]["limit_mb"], 1)
        self.assertEqual(self.ok(self.h.request("GET", "/api/issues/GAME-1/attachments")), [])
        self.ok(self.upload(b"\0" * (1024 * 1024), "exact.bin", "application/octet-stream"))

    def test_board_settings_name_the_limit(self):
        self.assertEqual(self.ok(self.h.request("GET", "/api/board"))["settings"]["max_attachment_mb"], 50)

    def _board(self):
        from earmark.board import Board

        return Board.open(db=self.db, actor="owner")


class RawTests(AttachmentRouteCase):
    def raw(self, att_id: int, method: str = "GET", **headers):
        return self.h.request(method, f"/api/attachments/{att_id}/raw", headers=headers)

    def test_inline_image_headers(self):
        att = self.ok(self.upload(PNG, "eye picker é.png"))
        resp = self.raw(att["id"])
        self.assertEqual((resp.status, resp.body), (200, PNG))
        h = resp.headers
        self.assertEqual(h["Content-Type"], "image/png")
        self.assertEqual(h["Content-Length"], str(len(PNG)))
        self.assertEqual(h["Content-Disposition"],
                         "inline; filename=\"eye picker _.png\"; filename*=UTF-8''eye%20picker%20%C3%A9.png")
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(h["Content-Security-Policy"], SANDBOX)
        self.assertEqual(h["ETag"], f'"{att["sha256"]}"')
        self.assertEqual(h["Cache-Control"], "private, max-age=31536000, immutable")
        self.assertEqual(h["Accept-Ranges"], "bytes")
        head = self.raw(att["id"], "HEAD")
        self.assertEqual((head.status, head.body, head.headers["Content-Length"]), (200, b"", str(len(PNG))))

    def test_inline_or_download_by_type(self):
        cases = [("clip.wav", b"RIFF", True, "audio/"), ("movie.mp4", b"mp4", True, "video/mp4"),
                 ("spec.pdf", b"%PDF-1.7", True, "application/pdf"),
                 ("run.log", b"line\n", True, "text/plain; charset=utf-8"),
                 ("page.html", b"<script>alert(1)</script>", False, "application/octet-stream"),
                 ("icon.svg", b"<svg onload=alert(1)/>", False, "application/octet-stream"),
                 ("data.json", b"{}", False, "application/octet-stream"),
                 ("tool.exe", b"MZ", False, "application/octet-stream")]
        for name, data, inline, ctype in cases:
            with self.subTest(name=name):
                att = self.ok(self.upload(data, name, "text/html"))
                resp = self.raw(att["id"])
                self.assertEqual(resp.body, data)
                self.assertTrue(resp.headers["Content-Type"].startswith(ctype), resp.headers["Content-Type"])
                self.assertTrue(resp.headers["Content-Disposition"].startswith(
                    "inline;" if inline else "attachment;"))
                self.assertEqual(resp.headers["Content-Security-Policy"], SANDBOX)
                self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")

    def test_ranges(self):
        data = bytes(range(100))
        att = self.ok(self.upload(data, "voice.ogg", "audio/ogg"))
        cases = [("bytes=0-9", 206, data[:10], "bytes 0-9/100"), ("bytes=90-", 206, data[90:], "bytes 90-99/100"),
                 ("bytes=-5", 206, data[95:], "bytes 95-99/100"), ("bytes=50-500", 206, data[50:], "bytes 50-99/100"),
                 ("bytes=0-1,5-6", 200, data, None), ("items=0-1", 200, data, None),
                 ("bytes=9-3", 200, data, None)]
        for value, status, body, content_range in cases:
            with self.subTest(range=value):
                resp = self.raw(att["id"], Range=value)
                self.assertEqual((resp.status, resp.body), (status, body))
                self.assertEqual(resp.headers["Content-Range"], content_range)
                self.assertEqual(resp.headers["Content-Length"], str(len(body)))
        for value in ("bytes=100-", "bytes=-0"):
            with self.subTest(range=value):
                resp = self.raw(att["id"], Range=value)
                self.err(resp, 416, "invalid_request")
                self.assertEqual(resp.headers["Content-Range"], "bytes */100")
        stale = self.raw(att["id"], Range="bytes=0-9", **{"If-Range": '"0000"'})
        self.assertEqual((stale.status, stale.body), (200, data))
        fresh = self.raw(att["id"], Range="bytes=0-9", **{"If-Range": f'"{att["sha256"]}"'})
        self.assertEqual(fresh.status, 206)

    def test_etag_revalidation(self):
        att = self.ok(self.upload(PNG))
        for value in (f'"{att["sha256"]}"', f'W/"{att["sha256"]}"', f'"x", "{att["sha256"]}"', "*"):
            with self.subTest(value=value):
                resp = self.raw(att["id"], **{"If-None-Match": value})
                self.assertEqual((resp.status, resp.body), (304, b""))
                self.assertEqual(resp.headers["ETag"], f'"{att["sha256"]}"')
        self.assertEqual(self.raw(att["id"], **{"If-None-Match": '"other"'}).status, 200)

    def test_missing_rows_and_files(self):
        self.err(self.raw(99), 404, "not_found")
        self.err(self.h.request("GET", "/api/attachments/99"), 404, "not_found")
        self.err(self.h.request("GET", "/api/attachments/abc/raw"), 404, "not_found")
        att = self.ok(self.upload(PNG))
        os.remove(att["path"])
        err = self.err(self.raw(att["id"]), 404, "not_found")
        self.assertEqual(err["hint"], "earmark doctor")


class DeleteTests(AttachmentRouteCase):
    def test_delete_removes_row_and_file(self):
        att = self.ok(self.upload(PNG))
        resp = self.h.request("DELETE", f"/api/attachments/{att['id']}")
        self.assertEqual(self.ok(resp), {"deleted": att["id"]})
        self.assertFalse(os.path.exists(att["path"]))
        self.err(self.h.request("DELETE", f"/api/attachments/{att['id']}"), 404, "not_found")
        self.err(self.h.request("DELETE", "/api/attachments/1", headers={"X-Earmark": None}), 403, "forbidden")
        resp = self.h.request("PUT", "/api/attachments/1")
        self.assertEqual((resp.status, resp.headers["Allow"]), (405, "DELETE, GET"))

    def test_owner_detaches_an_agents_file(self):
        agent = join_agent(self.db, "claude-ui", "worker")
        self.addCleanup(agent.close)
        att = agent.attach("GAME-1", [("a.txt", __import__("io").BytesIO(b"agent"))])[0]
        self.assertEqual(self.ok(self.h.request("DELETE", f"/api/attachments/{att['id']}")),
                         {"deleted": att["id"]})


if __name__ == "__main__":
    unittest.main()
