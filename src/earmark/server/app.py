"""`earmark ui`: a localhost HTTP server over the board service, plus static UI files.

One ThreadingHTTPServer, daemon threads. Every API request opens a Board acting as the board
owner and closes it afterwards. One watcher thread feeds Server-Sent Events. The server holds
no state of its own and writes nothing to the board unless a request asks for a change.
"""

from __future__ import annotations

import errno
import ipaddress
import json
import select
import signal
import socket
import sys
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

import earmark
from earmark.errors import Busy, EarmarkError, Forbidden, InternalError, InvalidRequest, NotFound
from earmark.server import api
from earmark.server.static import StaticFiles
from earmark.server.stream import Hub, Watcher

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7477
MAX_BODY = 1024 * 1024
MAX_STREAMS = 32
PING_SECONDS = 25.0
POLL_SECONDS = 1.0

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
       "form-action 'none'")

BoardFactory = Callable[[str | None], Any]


def _contract_version() -> str:
    return getattr(earmark, "CONTRACT_VERSION", "1.0.0")


def _default_factory(db_path: str | None) -> BoardFactory:
    def factory(actor: str | None) -> Any:
        from earmark.board import Board

        return Board.open(db=db_path, actor=actor)

    return factory


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_wildcard(host: str) -> bool:
    return host in ("", "0.0.0.0", "::")


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"earmark/{earmark.__version__}"
    sys_version = ""
    timeout = 60  # idle keep-alive connections give their thread back
    server: "EarmarkServer"
    _head = False
    _body_pending = False
    _command_name = "http"

    # ---- logging -----------------------------------------------------------------------
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        if self.server.verbose:
            sys.stderr.write("earmark ui: %s %s\n" % (self.address_string(), format % args))

    def log_error(self, format: str, *args: Any) -> None:  # noqa: A002
        self.log_message(format, *args)

    # ---- entry points ------------------------------------------------------------------
    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_OPTIONS(self) -> None:
        self._dispatch("OPTIONS")

    # ---- dispatch ----------------------------------------------------------------------
    def _dispatch(self, method: str) -> None:
        self._head = method == "HEAD"
        self._command_name = f"{method} {self.path.split('?', 1)[0]}"
        length = self.headers.get("Content-Length")
        self._body_pending = bool(self.headers.get("Transfer-Encoding")) or (
            length is not None and length.strip() not in ("", "0"))
        try:
            split = urlsplit(self.path)
            path = split.path or "/"
            if not self._host_ok():
                raise Forbidden("unexpected Host header",
                                hint=f"open http://127.0.0.1:{self.server.port}/")
            if path == api.STREAM_PATH and method == "GET":
                self._stream()
            elif path == "/api" or path.startswith("/api/"):
                self._api(method, path, split.query)
            else:
                self._static(method, path)
        except EarmarkError as exc:
            self._send_error(exc)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout):
            self.close_connection = True
        except Exception as exc:  # a bug: report it as internal_error, log it
            self._log_internal(exc)
            self._send_error(InternalError(f"internal error: {type(exc).__name__}"))

    def _log_internal(self, exc: BaseException) -> None:
        sys.stderr.write(f"earmark ui: internal error on {self._command_name}: "
                         f"{type(exc).__name__}: {exc}\n")
        if self.server.verbose:
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)

    # ---- security checks ---------------------------------------------------------------
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        return host in self.server.allowed_hosts

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        origin = origin.strip().lower()
        return origin.startswith("http://") and origin[len("http://"):] in self.server.allowed_hosts

    def _check_write(self) -> None:
        if self.headers.get("X-Earmark", "").strip() != "1":
            raise Forbidden("missing X-Earmark: 1 header", hint="send X-Earmark: 1 on every non-GET request")
        if not self._origin_ok():
            raise Forbidden("cross-origin request refused")

    def _read_body(self) -> Any:
        if self.headers.get("Transfer-Encoding"):
            raise InvalidRequest("chunked request bodies are not supported", hint="send Content-Length")
        raw_length = (self.headers.get("Content-Length") or "").strip()
        if raw_length in ("", "0"):
            return None
        try:
            length = int(raw_length)
        except ValueError:
            raise InvalidRequest("invalid Content-Length") from None
        if length < 0:
            raise InvalidRequest("invalid Content-Length")
        if length > MAX_BODY:
            raise _HTTPError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                             InvalidRequest(f"request body over {MAX_BODY // 1024} KiB"))
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            raise InvalidRequest("Content-Type must be application/json")
        data = self.rfile.read(length)
        self._body_pending = False
        if len(data) < length:
            self.close_connection = True
            raise InvalidRequest("request body ended early")
        try:
            return json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError) as exc:
            raise InvalidRequest(f"request body is not valid JSON: {exc}") from None

    # ---- API ---------------------------------------------------------------------------
    def _api(self, method: str, path: str, query: str) -> None:
        lookup = "GET" if method == "HEAD" else method
        route, raw_params, allowed = api.match(lookup, path)
        if route is None:
            if allowed:
                raise _HTTPError(HTTPStatus.METHOD_NOT_ALLOWED,
                                 InvalidRequest(f"{method} not allowed on {path}",
                                                hint="allowed: " + ", ".join(sorted(set(allowed)))),
                                 headers={"Allow": ", ".join(sorted(set(allowed)))})
            raise NotFound(f"no such endpoint: {path}", hint="see docs/HTTP_API.md")
        self._command_name = route.name
        body = None
        if lookup != "GET":
            self._check_write()
            body = self._read_body()
        params = {k: unquote(v) for k, v in raw_params.items()}
        qs = parse_qs(query, keep_blank_values=True)
        board = self.server.open_board()
        try:
            data = api.run(route, api.Request(board, params, qs, body))
        finally:
            try:
                board.close()
            except Exception:
                pass
        self._send_json(200, self.server.envelope(route.name, data=data))

    def _send_error(self, exc: EarmarkError) -> None:
        if isinstance(exc, _HTTPError):
            status, headers, err = exc.status, exc.headers, exc.error
        else:
            status, headers, err = exc.http_status, {}, exc
        self._send_json(status, self.server.envelope(self._command_name, error=err), headers=headers)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Protocol errors raised by the stdlib parser (400, 414, 431, 501) as JSON envelopes."""
        self.close_connection = True
        try:
            phrase = HTTPStatus(code).phrase
        except ValueError:
            phrase = "error"
        err = InvalidRequest(message or phrase)
        self._send_json(code, self.server.envelope(self._command_name, error=err))

    def _send_json(self, status: int, payload: dict, headers: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self._finish_headers()
        if not self._head:
            self.wfile.write(body)

    def _finish_headers(self, *, active: bool = False) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if active:
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Frame-Options", "DENY")
        if self._body_pending:
            # The request body was never read: the connection cannot carry another request.
            self.close_connection = True
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()

    # ---- static ------------------------------------------------------------------------
    def _static(self, method: str, path: str) -> None:
        if method not in ("GET", "HEAD"):
            raise _HTTPError(HTTPStatus.METHOD_NOT_ALLOWED,
                             InvalidRequest(f"{method} not allowed on {path}"),
                             headers={"Allow": "GET, HEAD"})
        static = self.server.static
        if path in ("/", "/index.html"):
            item = static.index()
        elif path.startswith("/assets/"):
            item = static.get(unquote(path[len("/assets/"):], errors="replace"))
        elif path == "/favicon.ico":
            item = static.get("favicon.ico") or static.get("favicon.svg")
        else:
            item = None
        if item is None:
            raise NotFound(f"not found: {path}")
        etag_match = self.headers.get("If-None-Match")
        if etag_match and item.etag in [t.strip() for t in etag_match.split(",")]:
            self.send_response(304)
            self.send_header("ETag", item.etag)
            self.send_header("Cache-Control", "no-cache")
            self._finish_headers(active=item.active)
            return
        self.send_response(200)
        self.send_header("Content-Type", item.content_type)
        self.send_header("Content-Length", str(len(item.data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("ETag", item.etag)
        self._finish_headers(active=item.active)
        if not self._head:
            self.wfile.write(item.data)

    # ---- Server-Sent Events ------------------------------------------------------------
    def _stream(self) -> None:
        hub = self.server.hub
        sub = hub.subscribe()  # Busy (503) past MAX_STREAMS
        try:
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self._finish_headers()
            self._write_event(b"retry: 3000\n\n")
            heads = hub.heads
            if heads is not None:
                self._write_change(heads)
            ping = self.server.ping_interval
            last_write = time.monotonic()
            conn = self.connection
            while not sub.closed:
                wait = ping - (time.monotonic() - last_write)
                if wait <= 0:
                    self._write_event(b": ping\n\n")
                    last_write = time.monotonic()
                    continue
                ready, _, _ = select.select([conn, sub], [], [], wait)
                if conn in ready:
                    try:
                        if not conn.recv(4096):
                            return  # client went away
                    except (BlockingIOError, InterruptedError):
                        pass
                if sub in ready:
                    heads = sub.take()
                    if sub.closed:
                        return
                    if heads is not None:
                        self._write_change(heads)
                        last_write = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return
        finally:
            hub.unsubscribe(sub)

    def _write_change(self, heads: dict) -> None:
        data = json.dumps({"event_id": heads["event_id"], "comment_id": heads["comment_id"]})
        self._write_event(f"event: change\ndata: {data}\n\n".encode("utf-8"))

    def _write_event(self, chunk: bytes) -> None:
        self.wfile.write(chunk)
        self.wfile.flush()


class _HTTPError(EarmarkError):
    """A transport-level status (405, 413) around an envelope error."""

    def __init__(self, status: int, error: EarmarkError, headers: dict | None = None):
        super().__init__(error.message, hint=error.hint, details=error.details)
        self.code = error.code
        self.status = int(status)
        self.error = error
        self.headers = headers or {}


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed")


class EarmarkServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = sys.platform != "win32"
    request_queue_size = 64

    def __init__(self, address: tuple, *, board_factory: BoardFactory, info: dict,
                 static: StaticFiles, verbose: bool = False, poll_interval: float = POLL_SECONDS,
                 ping_interval: float = PING_SECONDS, max_streams: int = MAX_STREAMS):
        self.address_family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        super().__init__(address, Handler)
        self.board_factory = board_factory
        self.owner = info.get("owner")
        self.prefix = info.get("prefix")
        self.static = static
        self.verbose = verbose
        self.ping_interval = ping_interval
        self.bind_host = address[0]
        self.port = self.server_address[1]
        self.allowed_hosts = _allowed_hosts(self.bind_host, self.port)
        self.hub = Hub(max_streams)
        self.watcher = Watcher(lambda: board_factory(None), self.hub, interval=poll_interval,
                               verbose=verbose)
        self._closed = False

    @property
    def url(self) -> str:
        host = self.bind_host
        if _is_wildcard(host):
            host = "127.0.0.1" if self.address_family == socket.AF_INET else "::1"
        return f"http://{_url_host(host)}:{self.port}/"

    @property
    def loopback(self) -> bool:
        return _is_loopback(self.bind_host)

    def open_board(self) -> Any:
        return self.board_factory(self.owner)

    def envelope(self, command: str, *, data: Any = None, error: EarmarkError | None = None) -> dict:
        return {
            "contract_version": _contract_version(),
            "ok": error is None,
            "data": data if error is None else None,
            "error": error.to_dict() if error is not None else None,
            "meta": {"command": command, "board": self.prefix, "actor": self.owner},
        }

    def start(self) -> None:
        """Start the watcher. serve_forever() runs the request loop."""
        self.watcher.start()

    def close(self) -> None:
        """Stop the watcher, end every stream and release the socket. Idempotent."""
        if self._closed:
            return
        self._closed = True
        self.hub.close()
        self.watcher.stop()
        self.server_close()

    def handle_error(self, request: Any, client_address: Any) -> None:
        if self.verbose:
            super().handle_error(request, client_address)


def _allowed_hosts(host: str, port: int) -> frozenset[str]:
    names = {"127.0.0.1", "localhost"}
    if ":" in host:
        names.add("[::1]")
    if not _is_loopback(host):
        if _is_wildcard(host):
            try:
                hostname = socket.gethostname()
                names.update({hostname.lower(), socket.getfqdn().lower()})
                names.update(socket.gethostbyname_ex(hostname)[2])
            except OSError:
                pass
        else:
            names.add(_url_host(host.lower()))
    allowed = {f"{name}:{port}" for name in names}
    if port == 80:
        allowed.update(names)
    return frozenset(allowed)


def make_server(db_path: str | None, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                verbose: bool = False, board_factory: BoardFactory | None = None,
                ui_dir: str | None = None, poll_interval: float = POLL_SECONDS,
                ping_interval: float = PING_SECONDS, max_streams: int = MAX_STREAMS) -> EarmarkServer:
    """Open the board, bind the socket and start the watcher; the caller runs serve_forever().

    Raises the board's errors (NoBoard, ...) and InvalidRequest when the port is taken.
    """
    factory = board_factory or _default_factory(db_path)
    first = factory(None)
    try:
        info = {"owner": getattr(first, "owner", None), "prefix": getattr(first, "prefix", None),
                "path": getattr(first, "path", None)}
    finally:
        first.close()
    if board_factory is None and info["path"]:
        factory = _default_factory(info["path"])  # pin the resolved file
    host = (host or DEFAULT_HOST).strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        server = EarmarkServer((host, port), board_factory=factory, info=info,
                               static=StaticFiles(ui_dir), verbose=verbose,
                               poll_interval=poll_interval, ping_interval=ping_interval,
                               max_streams=max_streams)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048:
            raise InvalidRequest(f"port {port} is already in use",
                                 hint=f"earmark ui --port {port + 1}") from None
        if exc.errno == errno.EACCES:
            raise InvalidRequest(f"permission denied listening on port {port}",
                                 hint=f"earmark ui --port {DEFAULT_PORT}") from None
        raise InvalidRequest(f"cannot listen on {host}:{port}: {exc.strerror or exc}",
                             hint=f"earmark ui --host {DEFAULT_HOST}") from None
    server.start()
    return server


def serve(db_path: str | None, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          open_browser: bool = False, verbose: bool = False) -> None:
    """Run `earmark ui` until Ctrl+C. Prints the URL once."""
    server = make_server(db_path, host=host, port=port, verbose=verbose)
    restore = _sigterm_as_interrupt()
    try:
        if not server.loopback:
            print(f"warning: listening on {server.bind_host}, not loopback: anyone who can reach "
                  f"this address can read and change the board as {server.owner}",
                  file=sys.stderr, flush=True)
        print(f"earmark ui: {server.url}  (board {server.prefix}, Ctrl+C to stop)", flush=True)
        if open_browser:
            threading.Thread(target=webbrowser.open, args=(server.url,), daemon=True).start()
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        restore()


def _sigterm_as_interrupt() -> Callable[[], None]:
    """Treat SIGTERM like Ctrl+C while serving (main thread only); returns the undo."""
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGTERM"):
        return lambda: None

    def handler(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    try:
        previous = signal.signal(signal.SIGTERM, handler)
    except (ValueError, OSError):
        return lambda: None
    return lambda: signal.signal(signal.SIGTERM, previous)
