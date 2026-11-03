#!/usr/bin/env python3
"""Build src/earmark/ui/icons.svg: a <symbol> sprite of the Lucide icons the web UI uses.

Usage:
    npm pack lucide-static@1.57.0            # in an empty directory; never install or run it
    tar -xzf lucide-static-1.57.0.tgz
    python3 -I scripts/build-icons.py package/icons [--out src/earmark/ui/icons.svg]

The icon files are treated as untrusted input: each one is parsed as XML and only plain shape
elements with geometry attributes are copied, so nothing executable or remote can reach the
sprite. Symbol ids are `i-<lucide name>`; the custom symbols below are original drawings.
Stroke styling (currentColor, width, caps) comes from the UI's CSS on the outer <svg>.
"""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET

# Icons referenced by the UI code. Keep sorted by area; build fails on a missing name.
LUCIDE = [
    # status
    "circle-dashed", "circle", "eye", "ban", "circle-check", "circle-x",
    # priority (urgent is the custom filled square below)
    "chevrons-up", "equal", "chevron-down", "minus",
    # comment kinds
    "message-square", "activity", "circle-help", "send", "check", "triangle-alert",
    # roles and agents
    "hammer", "scan-eye", "clipboard-list", "bot", "user-round", "terminal",
    # issue types
    "bug", "sparkles", "square-check", "wrench",
    # navigation and views
    "layers", "user", "inbox", "users", "list", "square-kanban", "layout-list", "hash",
    "folder", "folder-open", "folder-plus", "folder-tree", "history",
    # actions and controls
    "search", "plus", "x", "chevron-right", "chevron-left", "chevron-up",
    "ellipsis", "pencil", "trash-2", "archive", "archive-restore", "copy", "link", "link-2-off",
    "calendar", "tag", "list-tree", "corner-down-right", "corner-up-left", "clock",
    "hourglass", "arrow-up-down", "arrow-down-wide-narrow", "list-filter", "keyboard",
    "panel-left", "maximize-2", "minimize-2", "arrow-left", "arrow-right", "reply",
    "at-sign", "refresh-cw", "loader-circle", "info", "circle-alert", "move", "sun", "moon",
    "monitor", "grip-vertical", "external-link", "git-fork", "flag", "user-plus", "user-x",
    "eye-off", "text-quote", "check-check", "lock", "circle-dot", "workflow",
    # topic and profile icon palette
    "gamepad-2", "palette", "music", "audio-lines", "volume-2", "image", "brush", "code",
    "server", "database", "globe", "smartphone", "box", "cpu", "file-text", "type",
    "languages", "book-open", "shield", "zap", "rocket", "layout-dashboard", "film",
    "camera", "map", "swords", "star", "heart", "flame", "puzzle", "wand-sparkles",
    "headphones", "mic", "shapes", "scroll-text", "component", "network", "plug", "package",
    "smile", "app-window", "mouse-pointer-click", "git-pull-request", "settings-2",
]

# Original symbols: (id, inner markup). fill="currentColor" shapes take the icon color;
# shapes without a stroke attribute inherit the outer <svg> stroke set in CSS.
CUSTOM = [
    # in_progress: outline circle with the right half filled
    ("st-progress", '<circle cx="12" cy="12" r="10"/>'
                    '<path d="M12 6a6 6 0 0 1 0 12z" fill="currentColor" stroke="none"/>'),
    # priority urgent: a filled rounded square with an exclamation mark in the stroke color
    ("pri-urgent", '<rect x="2.5" y="2.5" width="19" height="19" rx="5" fill="currentColor" stroke="none"/>'
                   '<path d="M12 7v6"/><path d="M12 16.6v.4"/>'),
    # brand: a page with its corner folded down (an earmark)
    ("brand", '<path d="M6 2.5h8.5L20 8v11.5A2 2 0 0 1 18 21.5H6a2 2 0 0 1-2-2v-15a2 2 0 0 1 2-2z"/>'
              '<path d="M14.5 2.5V8H20" fill="currentColor"/>'),
]

SHAPES = {"path", "circle", "rect", "line", "polyline", "polygon", "ellipse", "g"}
ATTRS = {"d", "cx", "cy", "r", "rx", "ry", "x", "y", "x1", "y1", "x2", "y2", "width", "height",
         "points", "transform", "fill", "stroke", "opacity", "fill-rule", "clip-rule"}
NS = "{http://www.w3.org/2000/svg}"


def local(tag: str) -> str:
    return tag[len(NS):] if tag.startswith(NS) else tag


def esc(value: str) -> str:
    return (value.replace("&", "&amp;").replace('"', "&quot;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def clean(el: ET.Element, name: str) -> str:
    tag = local(el.tag)
    if tag not in SHAPES:
        raise SystemExit(f"{name}: unexpected element <{tag}>")
    attrs = []
    for key, value in el.attrib.items():
        key = local(key)
        if key in ATTRS:
            attrs.append(f'{key}="{esc(value)}"')
        elif key not in {"class"}:
            raise SystemExit(f"{name}: unexpected attribute {key} on <{tag}>")
    inner = "".join(clean(child, name) for child in el)
    head = f"<{tag}{' ' if attrs else ''}{' '.join(attrs)}"
    return f"{head}>{inner}</{tag}>" if inner else f"{head}/>"


def lucide_symbol(icons_dir: str, name: str) -> str:
    path = os.path.join(icons_dir, f"{name}.svg")
    if not os.path.isfile(path):
        raise SystemExit(f"missing icon: {name} (looked for {path})")
    root = ET.parse(path).getroot()
    if local(root.tag) != "svg":
        raise SystemExit(f"{name}: root is not <svg>")
    view_box = root.get("viewBox", "0 0 24 24")
    if view_box != "0 0 24 24":
        raise SystemExit(f"{name}: unexpected viewBox {view_box}")
    body = "".join(clean(child, name) for child in root)
    return f'<symbol id="i-{name}" viewBox="0 0 24 24">{body}</symbol>'


def main(argv: list[str] | None = None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    default_out = os.path.join(here, os.pardir, "src", "earmark", "ui", "icons.svg")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("icons_dir", help="lucide-static package/icons directory")
    parser.add_argument("--out", default=default_out, help="sprite path (default: the UI's icons.svg)")
    parser.add_argument("--version", default="1.57.0", help="lucide-static version, for the header")
    args = parser.parse_args(argv)

    names = sorted(set(LUCIDE))
    if len(names) != len(LUCIDE):
        dupes = sorted({n for n in LUCIDE if LUCIDE.count(n) > 1})
        print(f"note: duplicate names ignored: {', '.join(dupes)}", file=sys.stderr)
    symbols = [lucide_symbol(args.icons_dir, n) for n in names]
    symbols += [f'<symbol id="i-{sid}" viewBox="0 0 24 24">{body}</symbol>' for sid, body in CUSTOM]
    out = (
        f"<!-- earmark UI icons. Lucide {args.version} (ISC, see vendor/lucide.LICENSE.txt) and "
        "original symbols. Generated by scripts/build-icons.py; do not edit. -->\n"
        '<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true">\n'
        + "\n".join(symbols) + "\n</svg>\n"
    )
    out_path = os.path.normpath(args.out)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(out)
    print(f"{out_path}: {len(symbols)} symbols, {len(out.encode())} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
