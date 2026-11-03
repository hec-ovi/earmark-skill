"""HTTP server for `earmark ui`: the JSON API in docs/HTTP_API.md, live updates over
Server-Sent Events, and the static web UI. Localhost only, stdlib only, no SQL."""

from earmark.server.app import EarmarkServer, make_server, serve

__all__ = ["EarmarkServer", "make_server", "serve"]
