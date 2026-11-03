"""Default profiles and role loops, read from src/earmark/profiles/ with built-in fallbacks.

profiles/<slug>.md: a frontmatter block of `key: value` lines (name, role, icon) between
`---` lines, then the prompt body. profiles/loops/<role>.md: the loop text `join` prints.
"""

from __future__ import annotations

from earmark import model

BUILTIN_PROFILES = {
    "worker": {"name": "Worker", "role": "worker", "icon": "hammer",
               "prompt": "You are a worker. Take one issue at a time from your scope, finish it, "
                         "and hand it to review with what changed and how to verify it."},
    "reviewer": {"name": "Reviewer", "role": "reviewer", "icon": "shield-check",
                 "prompt": "You are a reviewer. Verify handoffs from other agents, then approve "
                           "or reject with concrete findings. Never review your own work."},
    "pm": {"name": "Project manager", "role": "pm", "icon": "clipboard-list",
           "prompt": "You are the project manager. Keep the board accurate: shape, assign and "
                     "rank issues so every agent has the right next issue."},
}

BUILTIN_LOOPS = {
    "worker": "Loop: earmark next → work → earmark comment KEY \"...\" --kind progress → "
              "earmark review KEY -m \"<what changed> · <how to verify>\"; exit 4: earmark inbox, "
              "then earmark wait --for work.",
    "reviewer": "Loop: earmark next --review → verify → earmark approve KEY -m \"...\" or "
                "earmark reject KEY -m \"...\"; exit 4: earmark wait --for review.",
    "pm": "Loop: earmark triage → shape, assign and rank → earmark inbox → earmark wait --for any.",
}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split `---\\nkey: value\\n---\\nbody` into ({key: value}, body). No YAML, by hand."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()
    meta: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return meta, "\n".join(lines[index + 1:]).strip()
        key, sep, value = line.partition(":")
        if sep and key.strip():
            meta[key.strip().lower()] = value.strip().strip("'\"")
    return {}, text.strip()


def _profiles_dir():
    try:
        from importlib.resources import files

        root = files("earmark").joinpath("profiles")
        return root if root.is_dir() else None
    except (ImportError, FileNotFoundError, OSError, TypeError):
        return None


def _read(node) -> str | None:
    try:
        return node.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None


def profile_seeds() -> list[dict]:
    """[{slug, name, role, icon, prompt}] from the package files, plus any missing built-in."""
    seeds: dict[str, dict] = {}
    root = _profiles_dir()
    entries = sorted(root.iterdir(), key=lambda n: n.name) if root is not None else []
    for node in entries:
        if not node.name.endswith(".md") or not node.is_file():
            continue
        seed = _seed_from_text(node.name[:-3], _read(node))
        if seed:
            seeds[seed["slug"]] = seed
    for slug, builtin in BUILTIN_PROFILES.items():
        seeds.setdefault(slug, {"slug": slug, **builtin})
    return [seeds[slug] for slug in sorted(seeds)]


def _seed_from_text(stem: str, text: str | None) -> dict | None:
    if text is None:
        return None
    meta, body = parse_frontmatter(text)
    slug = model.slugify(stem)
    role = meta.get("role", "").lower()
    if not slug or role not in model.ROLES:
        return None
    return {"slug": slug, "name": meta.get("name") or slug, "role": role,
            "icon": meta.get("icon") or None, "prompt": body}


def loop_text(role: str | None) -> str:
    role = role if role in model.ROLES else "worker"
    root = _profiles_dir()
    if root is not None:
        text = _read(root.joinpath("loops").joinpath(f"{role}.md"))
        if text and text.strip():
            return text.strip()
    return BUILTIN_LOOPS[role]
