"""Server-Sent Events: change broadcast, coalescing, pings, stream cap, disconnects, shutdown."""

from __future__ import annotations

import json
import threading
import time
import unittest

from tests.test_server_fixtures import FakeServerCase, FakeStore, SSEReader, ServerHarness  # first

from earmark.server.stream import Hub, Watcher  # noqa: E402


def wait_for(predicate, timeout: float = 3.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class StreamTests(FakeServerCase):
    def open_stream(self) -> SSEReader:
        self.assertTrue(self.h.server.watcher.ready.wait(3))
        reader = SSEReader(self.h.port)
        self.addCleanup(reader.close)
        return reader

    def test_headers_and_initial_heads(self):
        reader = self.open_stream()
        self.assertEqual(reader.status, 200)
        self.assertEqual(reader.headers["content-type"], "text/event-stream; charset=utf-8")
        self.assertEqual(reader.headers["cache-control"], "no-cache")
        self.assertNotIn("access-control-allow-origin", reader.headers)
        self.assertEqual(reader.next_block(), "retry: 3000")
        self.assertEqual(reader.next_change(), {"event_id": 10, "comment_id": 5})

    def test_change_is_broadcast_to_every_stream(self):
        a, b = self.open_stream(), self.open_stream()
        a.next_change()
        b.next_change()
        self.store.bump(event_id=11, comment_id=6)
        self.assertEqual(a.next_change(timeout=2), {"event_id": 11, "comment_id": 6})
        self.assertEqual(b.next_change(timeout=2), {"event_id": 11, "comment_id": 6})

    def test_change_without_new_heads_still_notifies(self):
        reader = self.open_stream()
        reader.next_change()
        self.store.bump()  # e.g. a comment edit: data_version moves, heads do not
        self.assertEqual(reader.next_change(timeout=2), {"event_id": 10, "comment_id": 5})

    def test_burst_coalesces(self):
        reader = self.open_stream()
        reader.next_change()
        for n in range(11, 30):
            self.store.bump(event_id=n)
        got = reader.next_change(timeout=2)
        while got["event_id"] != 29:
            got = reader.next_change(timeout=2)
        self.assertEqual(got["event_id"], 29)

    def test_bad_host_refused(self):
        reader = SSEReader(self.h.port, host="evil.example:80")
        self.addCleanup(reader.close)
        self.assertEqual(reader.status, 403)

    def test_other_sites_get_no_stream(self):
        for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                        {"Origin": "http://evil.example"}, {"Origin": "null"},
                        {"Origin": f"http://localhost:{self.h.port + 1}"}):
            with self.subTest(headers=headers):
                reader = SSEReader(self.h.port, headers=headers)
                self.addCleanup(reader.close)
                self.assertEqual(reader.status, 403)
                self.assertEqual(json.loads(reader.read_body())["error"]["code"], "forbidden")
        self.assertEqual(self.h.server.hub.count, 0)  # refused before taking a slot
        for headers in ({"Origin": f"http://127.0.0.1:{self.h.port}"}, {"Sec-Fetch-Site": "same-origin"}):
            with self.subTest(headers=headers):
                reader = SSEReader(self.h.port, headers=headers)
                self.addCleanup(reader.close)
                self.assertEqual(reader.status, 200)

    def test_disconnect_frees_the_slot(self):
        reader = self.open_stream()
        reader.next_change()
        self.assertTrue(wait_for(lambda: self.h.server.hub.count == 1))
        reader.close()
        self.assertTrue(wait_for(lambda: self.h.server.hub.count == 0, timeout=2))

    def test_watcher_reads_only(self):
        reader = self.open_stream()
        reader.next_change()
        self.store.bump(event_id=12)
        reader.next_change(timeout=2)
        self.assertEqual(self.store.names(), [])  # data_version/heads only: no board method calls


class StreamCapTests(FakeServerCase):
    harness_kwargs = {"max_streams": 2}

    def test_cap(self):
        readers = [SSEReader(self.h.port) for _ in range(2)]
        for r in readers:
            self.addCleanup(r.close)
            r.next_block()
        self.assertTrue(wait_for(lambda: self.h.server.hub.count == 2))
        extra = SSEReader(self.h.port)
        self.addCleanup(extra.close)
        self.assertEqual(extra.status, 503)
        body = extra.read_body()
        self.assertIn(b'"busy"', body)
        self.assertIn(b'"retriable":true', body)
        readers[0].close()
        self.assertTrue(wait_for(lambda: self.h.server.hub.count == 1, timeout=2))
        again = SSEReader(self.h.port)
        self.addCleanup(again.close)
        self.assertEqual(again.status, 200)


class PingTests(FakeServerCase):
    harness_kwargs = {"ping_interval": 0.3}

    def test_ping(self):
        reader = SSEReader(self.h.port)
        self.addCleanup(reader.close)
        reader.next_block()
        blocks = [reader.next_block(timeout=2) for _ in range(3)]
        self.assertIn(": ping", blocks)


class ShutdownTests(unittest.TestCase):
    def test_shutdown_ends_open_streams(self):
        store = FakeStore()
        h = ServerHarness(board_factory=store.factory)
        reader = SSEReader(h.port)
        try:
            reader.next_block()
            start = time.monotonic()
            h.stop()
            with self.assertRaises((EOFError, ConnectionError)):
                while True:
                    reader.next_block(timeout=3)
            self.assertLess(time.monotonic() - start, 3)
            self.assertFalse(h.server.watcher.is_alive())
            self.assertEqual(store.opened, store.closed)  # watcher connection closed too
        finally:
            reader.close()


class HubUnitTests(unittest.TestCase):
    def test_subscribe_cap_and_close(self):
        hub = Hub(max_streams=1)
        sub = hub.subscribe()
        from earmark.errors import Busy

        with self.assertRaises(Busy):
            hub.subscribe()
        hub.publish({"event_id": 3, "comment_id": None})
        self.assertEqual(sub.take(), {"event_id": 3, "comment_id": 0})
        self.assertIsNone(sub.take())
        hub.close()
        self.assertTrue(sub.closed)
        with self.assertRaises(Busy):
            hub.subscribe()
        hub.unsubscribe(sub)
        self.assertEqual(hub.count, 0)

    def test_watcher_survives_errors_and_polls_without_spinning(self):
        hub = Hub()
        calls = {"open": 0, "dv": 0}

        class Flaky:
            def data_version(self):
                calls["dv"] += 1
                if calls["dv"] == 2:
                    raise RuntimeError("database is locked")
                return 1

            def heads(self):
                return {"event_id": 1, "comment_id": 1}

            def close(self):
                pass

        def opener():
            calls["open"] += 1
            return Flaky()

        w = Watcher(opener, hub, interval=0.05)
        w.start()
        time.sleep(0.5)
        w.stop()
        self.assertFalse(w.is_alive())
        self.assertGreaterEqual(calls["open"], 2)  # reopened after the error
        self.assertLess(calls["dv"], 20)  # ~10 polls in 0.5 s at 0.05 s: no busy loop
        self.assertEqual(hub.heads, {"event_id": 1, "comment_id": 1})

    def test_notify_from_many_threads(self):
        hub = Hub()
        sub = hub.subscribe()
        threads = [threading.Thread(target=hub.publish, args=({"event_id": i, "comment_id": 0},))
                   for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertIsNotNone(sub.take())
        hub.unsubscribe(sub)


if __name__ == "__main__":
    unittest.main()
