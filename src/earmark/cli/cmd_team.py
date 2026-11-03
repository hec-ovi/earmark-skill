"""Team commands: join, leave, whoami, check, team, profiles, profile show/add/edit/rm, triage."""

from __future__ import annotations

TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations only; typing stays off the startup path
    from typing import Any

from earmark.errors import InvalidRequest

from earmark.cli import render as R
from earmark.cli.common import Context, Result


def cmd_join(ctx: Context, a) -> Result:
    b = ctx.board(identity=False)
    brief = b.join(a.profile, handle=ctx.actor_flag(), cli=a.cli)
    agent = brief.get("agent") or {} if isinstance(brief, dict) else {}
    ctx.actor = agent.get("handle") or ctx.actor_flag()
    return Result(brief, lambda: R.render_briefing(brief, style=ctx.style))


def cmd_leave(ctx: Context, a) -> Result:
    res = ctx.board().leave(keep=a.keep)
    return Result(res, lambda: R.render_leave(res, actor=ctx.actor))


def cmd_whoami(ctx: Context, a) -> Result:
    res = ctx.board().whoami()
    return Result(res, lambda: R.render_whoami(res, style=ctx.style))


def cmd_check(ctx: Context, a) -> Result:
    b = ctx.board()
    check = getattr(b, "check", None)
    if check is None:
        raise NotImplementedError("earmark check needs a board with the check feed")
    res = check(peek=a.peek)
    return Result(res, lambda: R.render_check(res))


def cmd_team(ctx: Context, a) -> Result:
    b = ctx.board()
    res = b.team()
    return Result(res, lambda: R.render_team(res, owner=getattr(b, "owner", None), style=ctx.style))


def cmd_profiles(ctx: Context, a) -> Result:
    res = ctx.board().profiles()
    return Result(res, lambda: R.render_profiles(res, style=ctx.style))


def cmd_profile_show(ctx: Context, a) -> Result:
    p = ctx.board().get_profile(a.slug)
    return Result(p, lambda: R.render_profile(p, style=ctx.style))


def cmd_profile_add(ctx: Context, a) -> Result:
    prompt = ctx.read_text(a.prompt, a.prompt_file, "prompt")
    kw: dict[str, Any] = {"name": a.name, "role": a.role, "prompt": prompt or "", "topics": a.topic}
    if a.icon is not None:
        kw["icon"] = a.icon
    p = ctx.board().add_profile(a.slug, **kw)
    return Result(p, lambda: f"profile {p.get('slug', a.slug)} added")


def cmd_profile_edit(ctx: Context, a) -> Result:
    fields: dict[str, Any] = {}
    for name in ("name", "role", "icon"):
        if getattr(a, name) is not None:
            fields[name] = getattr(a, name)
    prompt = ctx.read_text(a.prompt, a.prompt_file, "prompt")
    if prompt is not None:
        fields["prompt"] = prompt
    if a.topic and a.whole_board:
        raise InvalidRequest("profile edit: give -t PATH or --whole-board, not both")
    if a.topic:
        fields["topics"] = a.topic
    if a.whole_board:
        fields["topics"] = []
    if not fields:
        raise InvalidRequest("profile edit: nothing to change",
                             hint=f"earmark profile edit {a.slug} --name N | --role R | --prompt T | -t PATH")
    p = ctx.board().edit_profile(a.slug, if_version=a.if_version, **fields)
    return Result(p, lambda: f"profile {p.get('slug', a.slug)} updated: {', '.join(fields)}"
                             f"{R.SEP}version {R.dash(p.get('version'))}")


def cmd_profile_rm(ctx: Context, a) -> Result:
    res = ctx.board().remove_profile(a.slug)
    return Result(res, lambda: f"profile {a.slug} removed")


def cmd_triage(ctx: Context, a) -> Result:
    if a.limit < 1:
        from earmark.errors import InvalidRequest
        raise InvalidRequest("triage: --limit N must be 1 or more")
    res = ctx.board().triage(limit=a.limit)
    return Result(res, lambda: R.render_triage(res, style=ctx.style))
