"""Static checks for the web UI in src/earmark/ui/: every referenced asset exists, nothing loads
from the network, the page fits the strict CSP, icons used by the code are in the sprite, and the
payload stays small. Stdlib only."""

from __future__ import annotations

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "src", "earmark", "ui")
CODE_EXT = (".html", ".js", ".css", ".svg")
# XML namespace identifiers are names, not fetches.
NAMESPACES = {
    "http://www.w3.org/2000/svg",
    "http://www.w3.org/1999/xlink",
    "http://www.w3.org/1999/xhtml",
    "http://www.w3.org/1998/Math/MathML",
}
URL_RE = re.compile(r"""(?:https?:)?//[a-z0-9.-]+\.[a-z]{2,}[^\s'"`)<>]*""", re.I)
IMPORT_RE = re.compile(r"""(?:\bimport\s*(?:[^'"();]*?\bfrom\s*)?|\bimport\s*\(\s*|\bexport\s*[^'";]*?\bfrom\s*)['"]([^'"]+)['"]""")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def ui_files(ext=CODE_EXT):
    for base, _dirs, files in os.walk(UI):
        for name in files:
            if name.endswith(ext):
                yield os.path.join(base, name)


def asset_path(url: str) -> str:
    """Map a served URL to the file under src/earmark/ui/."""
    if url in ("/", "/index.html"):
        return os.path.join(UI, "index.html")
    assert url.startswith("/assets/"), url
    return os.path.join(UI, *url[len("/assets/"):].split("/"))


class UiAssetTests(unittest.TestCase):
    def test_index_references_exist(self):
        html = read(os.path.join(UI, "index.html"))
        refs = re.findall(r"""\b(?:src|href)=["']([^"']+)["']""", html)
        self.assertTrue(refs, "index.html references no assets")
        for ref in refs:
            with self.subTest(ref=ref):
                self.assertTrue(ref.startswith("/assets/"), f"{ref} is not served from /assets/")
                self.assertTrue(os.path.isfile(asset_path(ref)), f"missing asset {ref}")

    def test_index_fits_the_csp(self):
        html = read(os.path.join(UI, "index.html"))
        for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", html, re.S | re.I):
            self.assertIn("src=", m.group(1), "inline <script> is blocked by script-src 'self'")
            self.assertEqual(m.group(2).strip(), "", "inline script body")
        self.assertNotRegex(html, r"<style\b", "inline <style> is blocked by style-src 'self'")
        self.assertNotRegex(html, r"\sstyle\s*=", "style attributes are blocked by style-src 'self'")
        self.assertNotRegex(html, r"\son[a-z]+\s*=", "inline event handlers are blocked by script-src 'self'")

    def test_module_graph_resolves(self):
        entry = os.path.join(UI, "js", "main.js")
        seen, todo = set(), [entry]
        while todo:
            path = os.path.normpath(todo.pop())
            if path in seen:
                continue
            seen.add(path)
            self.assertTrue(os.path.isfile(path), f"missing module {os.path.relpath(path, UI)}")
            for spec in IMPORT_RE.findall(read(path)):
                with self.subTest(module=os.path.relpath(path, UI), spec=spec):
                    self.assertTrue(spec.startswith("./") or spec.startswith("../"),
                                    f"bare or absolute specifier {spec!r} needs an import map (blocked by the CSP)")
                    todo.append(os.path.join(os.path.dirname(path), spec))
        modules = {os.path.normpath(p) for p in ui_files((".js",)) if os.sep + "js" + os.sep in p}
        modules.discard(os.path.normpath(os.path.join(UI, "js", "theme-init.js")))
        self.assertFalse(modules - seen, f"modules nobody imports: {sorted(os.path.relpath(p, UI) for p in modules - seen)}")

    def test_no_external_urls(self):
        for path in ui_files():
            text = read(path)
            for url in URL_RE.findall(text):
                bare = url.rstrip(".,;")
                if bare in NAMESPACES or ("http:" + bare) in NAMESPACES:
                    continue
                self.fail(f"{os.path.relpath(path, UI)} references an external URL: {url}")
            self.assertNotRegex(text, r"@import\s+url\(\s*['\"]?https?:", path)

    def test_css_urls_are_local(self):
        for path in ui_files((".css",)):
            for ref in re.findall(r"url\(\s*['\"]?([^'\")]+)", read(path)):
                with self.subTest(css=os.path.relpath(path, UI), ref=ref):
                    self.assertFalse(re.match(r"^[a-z]+:", ref) and not ref.startswith("data:"), ref)

    def test_icons_used_by_the_code_are_in_the_sprite(self):
        sprite = read(os.path.join(UI, "icons.svg"))
        symbols = set(re.findall(r'<symbol id="i-([a-z0-9-]+)"', sprite))
        self.assertGreater(len(symbols), 20)
        used = set()
        for path in ui_files((".js",)):
            if os.sep + "vendor" + os.sep in path:
                continue
            text = read(path)
            used |= set(re.findall(r"""\bicon(?:Name)?\s*[:=]\s*['"]([a-z0-9-]+)['"]""", text))
            used |= set(re.findall(r"""<\$\{Icon\}\s+name=["']([a-z0-9-]+)["']""", text))
            used |= set(re.findall(r"""<\$\{(?:IconButton)\}\s+icon=["']([a-z0-9-]+)["']""", text))
            block = re.search(r"ICON_CHOICES\s*=\s*\[(.*?)\]", text, re.S)
            if block:
                used |= set(re.findall(r"'([a-z0-9-]+)'", block.group(1)))
        missing = sorted(used - symbols)
        self.assertFalse(missing, f"icons used but not in icons.svg (add them to scripts/build-icons.py): {missing}")

    def test_vendored_code_has_licenses(self):
        vendor = os.path.join(UI, "vendor")
        for name in ("preact.LICENSE.txt", "htm.LICENSE.txt", "lucide.LICENSE.txt", "VENDOR.txt"):
            self.assertTrue(os.path.isfile(os.path.join(vendor, name)), name)
        for path in ui_files((".js",)):
            if os.sep + "vendor" + os.sep in path:
                self.assertNotIn("sourceMappingURL", read(path), path)

    def test_payload_budget(self):
        # Unminified source served from 127.0.0.1 (no build step); about 70 KB gzipped today.
        # The ceiling guards against accidental bloat such as a vendored framework or a data dump.
        total = 0
        for path in ui_files((".html", ".js", ".css")):
            total += os.path.getsize(path)
        self.assertLess(total, 320 * 1024, f"UI payload {total} bytes (ceiling 320 KB, sprite excluded)")


if __name__ == "__main__":
    unittest.main()
