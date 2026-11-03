"""Static files for the web UI: src/earmark/ui/ served under / and /assets/.

Paths are resolved inside one root directory and never outside it. Every file goes out with
`Cache-Control: no-cache` and a content ETag, so the browser revalidates and gets a 304 when
nothing changed; there is no build step and no fingerprinting.
"""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
}

# Documents that can run script in the page origin get the strict CSP.
ACTIVE_TYPES = (".html", ".htm", ".svg")

PLACEHOLDER = b"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>earmark</title>
</head>
<body>
<h1>earmark</h1>
<p>The board API is running, but the web UI files are not installed
(<code>src/earmark/ui/index.html</code> is missing).</p>
<p>Board summary: <a href="/api/board">/api/board</a></p>
</body>
</html>
"""


def default_ui_dir() -> Path:
    """The package's ui/ directory, installed or in a checkout."""
    try:
        from importlib.resources import files

        res = files("earmark") / "ui"
        if isinstance(res, Path):
            return res
    except Exception:  # pragma: no cover - zipped or unusual installs
        pass
    return Path(__file__).resolve().parent.parent / "ui"


def content_type(name: str) -> str:
    return CONTENT_TYPES.get(os.path.splitext(name)[1].lower(), "application/octet-stream")


class StaticFile:
    __slots__ = ("data", "etag", "content_type", "active")

    def __init__(self, data: bytes, etag: str, ctype: str, active: bool):
        self.data = data
        self.etag = etag
        self.content_type = ctype
        self.active = active


class StaticFiles:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(root) if root is not None else default_ui_dir()
        self._cache: dict[str, tuple[int, int, StaticFile]] = {}
        self._lock = threading.Lock()

    def _root(self) -> Path:
        # Resolved per call: the directory may appear after the server starts.
        return self.root.resolve()

    def resolve(self, rel: str) -> Path | None:
        """Map a decoded relative path to a file inside the root, or None.

        Rejects empty, dot and dot-dot segments, hidden files, backslashes, NUL bytes and
        anything that resolves (through symlinks too) outside the root.
        """
        if not rel or "\x00" in rel or "\\" in rel:
            return None
        parts = rel.split("/")
        for part in parts:
            if part in ("", ".", "..") or part.startswith("."):
                return None
        root = self._root()
        try:
            candidate = root.joinpath(*parts).resolve()
        except (OSError, RuntimeError, ValueError):
            return None
        if candidate != root and root not in candidate.parents:
            return None
        if not candidate.is_file():
            return None
        return candidate

    def load(self, path: Path) -> StaticFile | None:
        try:
            st = path.stat()
        except OSError:
            return None
        key = str(path)
        with self._lock:
            hit = self._cache.get(key)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
        try:
            data = path.read_bytes()
        except OSError:
            return None
        etag = '"' + hashlib.sha256(data).hexdigest()[:20] + '"'
        ext = path.suffix.lower()
        item = StaticFile(data, etag, content_type(path.name), ext in ACTIVE_TYPES)
        with self._lock:
            self._cache[key] = (st.st_mtime_ns, st.st_size, item)
        return item

    def get(self, rel: str) -> StaticFile | None:
        path = self.resolve(rel)
        return self.load(path) if path is not None else None

    def index(self) -> StaticFile:
        item = self.get("index.html")
        if item is not None:
            return item
        etag = '"placeholder-' + hashlib.sha256(PLACEHOLDER).hexdigest()[:12] + '"'
        return StaticFile(PLACEHOLDER, etag, CONTENT_TYPES[".html"], True)
