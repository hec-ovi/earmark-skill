"""Topic commands: topic add, edit, mv, rm."""

from __future__ import annotations

from earmark.errors import InvalidRequest

from earmark.cli.common import Context, Result


def _topic_fields(a) -> dict:
    return {k: v for k, v in (("name", a.name), ("icon", a.icon), ("description", a.description))
            if v is not None}


def cmd_topic_add(ctx: Context, a) -> Result:
    t = ctx.board().add_topic(a.path, **_topic_fields(a))
    return Result(t, lambda: f"topic {t.get('path', a.path)} added")


def cmd_topic_edit(ctx: Context, a) -> Result:
    fields = _topic_fields(a)
    if not fields:
        raise InvalidRequest("topic edit: nothing to change",
                             hint=f"earmark topic edit {a.path} --name N | --icon I | --description D")
    t = ctx.board().edit_topic(a.path, **fields)
    return Result(t, lambda: f"topic {t.get('path', a.path)} updated: {', '.join(fields)}")


def cmd_topic_mv(ctx: Context, a) -> Result:
    t = ctx.board().move_topic(a.path, a.new_path)
    return Result(t, lambda: f"topic {a.path} moved to {t.get('path', a.new_path)}")


def cmd_topic_rm(ctx: Context, a) -> Result:
    res = ctx.board().remove_topic(a.path, archive=a.archive)
    return Result(res, lambda: f"topic {a.path} {'archived' if a.archive else 'removed'}")
