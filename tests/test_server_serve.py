"""serve(): prints the URL once, refuses a taken port, warns off loopback, exits cleanly."""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import unittest
from unittest import mock

from tests.test_server_fixtures import _SRC, FakeStore, errors  # first: puts src/ on sys.path

from earmark.server import app, make_server, serve  # noqa: E402

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _stop_immediately(store_box: dict):
    def fake_serve_forever(self, poll_interval=0.5):
        store_box["server"] = self
        store_box["watcher_alive"] = self.watcher.is_alive()
        raise KeyboardInterrupt

    return fake_serve_forever


class ServeTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        patcher = mock.patch.object(app, "_default_factory", lambda db: self.store.factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_serve(self, **kw) -> tuple[str, str, dict]:
        box: dict = {}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(app.EarmarkServer, "serve_forever", _stop_immediately(box)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            serve("/nonexistent/earmark.db", port=0, **kw)
        return out.getvalue(), err.getvalue(), box

    def test_prints_url_once_and_cleans_up(self):
        before = signal.getsignal(signal.SIGTERM)
        out, err, box = self.run_serve()
        server = box["server"]
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 1, out)
        self.assertIn(f"http://127.0.0.1:{server.port}/", lines[0])
        self.assertEqual(err, "")
        self.assertTrue(box["watcher_alive"])
        self.assertFalse(server.watcher.is_alive())
        self.assertEqual(server.socket.fileno(), -1)  # socket released
        self.assertEqual(self.store.opened, self.store.closed)
        self.assertIs(signal.getsignal(signal.SIGTERM), before)

    def test_open_browser(self):
        with mock.patch.object(app.webbrowser, "open") as opener:
            _, _, box = self.run_serve(open_browser=True)
            for t in threading.enumerate():
                if t.name == "earmark-browser":
                    t.join(1)
        opener.assert_called_once_with(box["server"].url)

    def test_non_loopback_warns(self):
        out, err, box = self.run_serve(host="0.0.0.0")
        self.assertIn("warning", err)
        self.assertIn("0.0.0.0", err)
        self.assertIn(f"http://127.0.0.1:{box['server'].port}/", out)

    def test_loopback_hosts_do_not_warn(self):
        for host in ("127.0.0.1", "localhost"):
            with self.subTest(host=host):
                _, err, _ = self.run_serve(host=host)
                self.assertEqual(err, "")

    def test_port_in_use(self):
        taken = socket.socket()
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        self.addCleanup(taken.close)
        port = taken.getsockname()[1]
        with self.assertRaises(errors.InvalidRequest) as ctx:
            make_server(None, port=port, board_factory=self.store.factory)
        self.assertIn(str(port), ctx.exception.message)
        self.assertEqual(ctx.exception.hint, f"earmark ui --port {port + 1}")
        self.assertEqual(self.store.opened, self.store.closed)

    def test_board_errors_propagate(self):
        def broken(actor):
            raise errors.NoBoard("no board found", hint="earmark init")

        with self.assertRaises(errors.NoBoard):
            make_server(None, port=0, board_factory=broken)

    def test_ipv6_loopback(self):
        if not socket.has_ipv6:
            self.skipTest("no IPv6")
        try:
            server = make_server(None, host="::1", port=0, board_factory=self.store.factory)
        except errors.InvalidRequest:
            self.skipTest("::1 not available")
        try:
            self.assertEqual(server.url, f"http://[::1]:{server.port}/")
            self.assertIn(f"[::1]:{server.port}", server.allowed_hosts)
            self.assertTrue(server.loopback)
        finally:
            server.close()


CHILD = textwrap.dedent("""
    import sys
    sys.path[:0] = [{src!r}, {root!r}]
    from tests.test_server_fixtures import FakeStore
    from earmark.server import app
    store = FakeStore()
    app._default_factory = lambda db: store.factory
    app.serve("/nonexistent/earmark.db", port=0)
    print("exited", store.opened == store.closed, flush=True)
""")


class ProcessTests(unittest.TestCase):
    def _run_and_signal(self, sig: int) -> tuple[int, str, str]:
        code = CHILD.format(src=_SRC, root=os.path.dirname(TESTS_DIR))
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        try:
            first = proc.stdout.readline()
            self.assertIn("http://127.0.0.1:", first)
            port = int(first.split("http://127.0.0.1:")[1].split("/")[0])
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/board")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            self.assertTrue(json.loads(resp.read())["ok"])
            conn.close()
            proc.send_signal(sig)
            out, err = proc.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        return proc.returncode, first + out, err

    @unittest.skipIf(sys.platform == "win32", "POSIX signals")
    def test_ctrl_c_exits_cleanly(self):
        code, out, err = self._run_and_signal(signal.SIGINT)
        self.assertEqual(code, 0, err)
        self.assertIn("exited True", out)
        self.assertNotIn("Traceback", err)
        self.assertEqual(out.count("http://"), 1)

    @unittest.skipIf(sys.platform == "win32", "POSIX signals")
    def test_sigterm_exits_cleanly(self):
        code, out, err = self._run_and_signal(signal.SIGTERM)
        self.assertEqual(code, 0, err)
        self.assertIn("exited True", out)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
