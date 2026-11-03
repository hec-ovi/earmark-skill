"""`earmark ui`: a localhost HTTP server over the board service, plus static UI files.

One ThreadingHTTPServer, daemon threads. Every API request opens a Board acting as the board
owner and closes it afterwards. One watcher thread feeds Server-Sent Events. The server holds
no state of its own and writes nothing to the board unless a request asks for a change.
"""

from __future__ import annotations

import errno
import ipaddress
import json
import os
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
from urllib.parse import parse_qs, quote, unquote, urlsplit

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

DRAIN_SECONDS = 5.0     # reading an unread request body after an early answer, at most
DRAIN_READ_TIMEOUT = 1.0
FILE_CHUNK = 64 * 1024

CSP = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self'; style-src 'self'; "
       "script-src 'self'; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'none'")
# Attachment bytes: never scripts, never the page origin's document.
FILE_CSP = "sandbox; default-src 'none'"
FILE_CACHE = "private, max-age=31536000, immutable"

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
    _body_left: int | None = None
    _responded = False
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
        self._responded = False
        self._command_name = f"{method} {self.path.split('?', 1)[0]}"
        length = self.headers.get("Content-Length")
        self._body_pending = bool(self.headers.get("Transfer-Encoding")) or (
            length is not None and length.strip() not in ("", "0"))
        self._body_left = _length(length) if not self.headers.get("Transfer-Encoding") else None
        try:
            split = urlsplit(self.path)
            path = split.path or "/"
            if not self._host_ok():
                raise Forbidden("unexpected Host header",
                                hint=f"open http://127.0.0.1:{self.server.port}/")
            is_api = path == "/api" or path.startswith("/api/")
            if is_api and not self._fetch_site_ok():
                raise Forbidden("request from another site refused",
                                hint=f"open http://127.0.0.1:{self.server.port}/")
            if path == api.STREAM_PATH and method == "GET":
                if not self._origin_ok():
                    raise Forbidden("cross-origin request refused")
                self._stream()
            elif is_api:
                self._api(method, path, split.query)
            else:
                self._static(method, path)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout):
            self.close_connection = True
        except EarmarkError as exc:
            self._fail(exc)
        except Exception as exc:  # a bug: report it as internal_error, log it
            self._log_internal(exc)
            self._fail(InternalError(f"internal error: {type(exc).__name__}"))
        if self._body_pending and self._responded:
            self._drain_body()

    def _drain_body(self) -> None:
        """After an early answer (413, 403, 404, ...) to a request whose body was not read, read
        and drop the rest (up to Content-Length, at most DRAIN_SECONDS) before the connection
        closes. Closing with unread bytes makes the kernel reset the connection, and the client,
        still sending, would see that reset instead of the answer."""
        left = self._body_left
        deadline = time.monotonic() + DRAIN_SECONDS
        self.close_connection = True
        try:
            self.wfile.flush()
            self.connection.settimeout(DRAIN_READ_TIMEOUT)
            while (left is None or left > 0) and time.monotonic() < deadline:
                chunk = self.rfile.read1(FILE_CHUNK if left is None else min(FILE_CHUNK, left))
                if not chunk:
                    break
                if left is not None:
                    left -= len(chunk)
        except (OSError, ValueError):
            pass
        self._body_pending = False

    def _fail(self, exc: EarmarkError) -> None:
        if self._responded:  # headers already went out: all we can do is drop the connection
            self.close_connection = True
            return
        self._send_error(exc)

    def _log_internal(self, exc: BaseException) -> None:
        sys.stderr.write(f"earmark ui: internal error on {self._command_name}: "
                         f"{type(exc).__name__}: {exc}\n")
        if self.server.verbose:
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)

    # ---- security checks ---------------------------------------------------------------
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        return host in self.server.allowed_hosts

    def _fetch_site_ok(self) -> bool:
        """Browsers say who sent a request: the UI's own page (same-origin) or the user typing
        the URL (none). Another site, or another port on this host (same-site), may not use the
        API, not even GETs, which would hold stream slots or load the board for it."""
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        return site in ("", "same-origin", "none")

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
        self._body_left = 0
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
        body = stream = None
        if lookup != "GET":
            self._check_write()
            if route.stream:
                stream = self._body_stream()
            else:
                body = self._read_body()
        params = {k: unquote(v) for k, v in raw_params.items()}
        qs = parse_qs(query, keep_blank_values=True)
        board = self.server.open_board()
        try:
            if stream is not None:
                self._check_upload_size(board, stream.left, qs)
            data = api.run(route, api.Request(board, params, qs, body, stream, self.headers))
        finally:
            try:
                board.close()
            except Exception:
                pass
        if isinstance(data, api.RawFile):
            self._send_file(data)
        else:
            self._send_json(200, self.server.envelope(route.name, data=data))

    # ---- uploads -----------------------------------------------------------------------
    def _body_stream(self) -> "_BodyStream":
        """The raw request body for upload routes: Content-Length required, read on demand."""
        if self.headers.get("Transfer-Encoding"):
            raise InvalidRequest("chunked request bodies are not supported", hint="send Content-Length")
        raw_length = (self.headers.get("Content-Length") or "").strip()
        if raw_length == "":
            raise _HTTPError(HTTPStatus.LENGTH_REQUIRED,
                             InvalidRequest("Content-Length is required",
                                            hint="send the file bytes as the body, with Content-Length"))
        length = _length(raw_length)
        if length is None:
            raise InvalidRequest("invalid Content-Length")
        return _BodyStream(self, length)

    @staticmethod
    def _check_upload_size(board: Any, length: int, qs: dict) -> None:
        """413 before reading a byte when Content-Length is over the board's limit."""
        limit_mb = getattr(board, "max_attachment_mb", None)
        if isinstance(limit_mb, int) and length > limit_mb * 1024 * 1024:
            from earmark.attachments import too_large

            name = (qs.get("name") or ["the file"])[-1] or "the file"
            raise too_large(name, limit_mb, length)

    # ---- attachment bytes --------------------------------------------------------------
    def _send_file(self, item: "api.RawFile") -> None:
        """An attachment's bytes: ETag (its sha256) with If-None-Match → 304, one Range → 206,
        inline only for types a browser shows without running them; always sandboxed."""
        etag = f'"{item.sha256}"'
        if _etag_matches(self.headers.get("If-None-Match"), item.sha256):
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", FILE_CACHE)
            self._finish_headers()
            return
        try:
            fh = open(item.path, "rb")
        except FileNotFoundError:
            raise NotFound(f"the file of {item.name} is missing from {item.path}",
                           hint="earmark doctor") from None
        with fh:
            size = os.fstat(fh.fileno()).st_size
            wanted = self.headers.get("Range")
            if_range = self.headers.get("If-Range")
            if wanted and if_range and not _etag_matches(if_range, item.sha256):
                wanted = None  # the client's copy is another version: send all of it
            span = _parse_range(wanted, size) if wanted else None
            if span == "unsatisfiable":
                raise _HTTPError(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                                 InvalidRequest(f"range {wanted} is outside the file ({size} bytes)"),
                                 headers={"Content-Range": f"bytes */{size}"})
            start, end = span if span else (0, size - 1)
            inline = inline_type(item.mime)
            self.send_response(206 if span else 200)
            if inline:
                ctype = item.mime + ("; charset=utf-8" if item.mime == "text/plain" else "")
            else:
                ctype = "application/octet-stream"
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(max(0, end - start + 1)))
            if span:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Disposition", _disposition("inline" if inline else "attachment",
                                                                 item.name))
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", FILE_CACHE)
            self.send_header("Content-Security-Policy", FILE_CSP)
            self._finish_headers()
            if self._head or size == 0:
                return
            fh.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = fh.read(min(FILE_CHUNK, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

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
        self._responded = True

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
        etag_match = (self.headers.get("If-None-Match") or "").strip()
        if etag_match == "*" or item.etag in [t.strip() for t in etag_match.split(",")]:
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


class _BodyStream:
    """The request body as a binary stream that never reads past Content-Length."""

    def __init__(self, handler: Handler, length: int):
        self.handler = handler
        self.left = length
        if length == 0:
            handler._body_pending = False

    def read(self, n: int = -1) -> bytes:
        if self.left <= 0:
            return b""
        want = self.left if n is None or n < 0 else min(n, self.left)
        data = self.handler.rfile.read(want)
        if not data:
            self.handler.close_connection = True
            self.handler._body_pending = False
            raise InvalidRequest("request body ended early")
        self.left -= len(data)
        self.handler._body_left = self.left
        if self.left == 0:
            self.handler._body_pending = False
        return data


def _length(raw: str | None) -> int | None:
    try:
        value = int((raw or "").strip())
    except ValueError:
        return None
    return value if value >= 0 else None


INLINE_MAJOR = ("image/", "audio/", "video/")


def inline_type(mime: str) -> bool:
    """Shown in the page: images (not SVG, which can carry script), audio, video, plain text.
    PDFs download: the sandbox CSP every file carries keeps Chrome's PDF viewer from loading."""
    if mime == "image/svg+xml":
        return False
    return mime.startswith(INLINE_MAJOR) or mime == "text/plain"


def _disposition(kind: str, name: str) -> str:
    fallback = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_" for ch in name)
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def _etag_matches(header: str | None, sha256: str) -> bool:
    if not header:
        return False
    for tag in header.split(","):
        tag = tag.strip()
        if tag == "*":
            return True
        if tag.startswith("W/"):
            tag = tag[2:]
        if tag.strip('"') == sha256:
            return True
    return False


def _parse_range(header: str, size: int):
    """(start, end) for one `bytes=` range, 'unsatisfiable', or None to ignore the header
    (malformed, another unit, or several ranges: the whole file is sent)."""
    unit, _, spec = header.partition("=")
    if unit.strip().lower() != "bytes" or "," in spec:
        return None
    first, dash, last = spec.strip().partition("-")
    if not dash:
        return None
    first, last = first.strip(), last.strip()
    if not first:  # suffix: the last N bytes
        if not last.isdigit():
            return None
        n = int(last)
        if n == 0:
            return "unsatisfiable"
        return (max(0, size - n), size - 1) if size else "unsatisfiable"
    if not first.isdigit() or (last and not last.isdigit()):
        return None
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size:
        return "unsatisfiable"
    if end < start:
        return None
    return start, end


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
            threading.Thread(target=webbrowser.open, args=(server.url,), daemon=True,
                             name="earmark-browser").start()
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
