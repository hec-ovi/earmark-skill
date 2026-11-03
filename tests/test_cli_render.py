"""Pure renderers against fixture dicts: the exact samples of commands.md, plus edge cases."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cli_support import (COMMENTS, DAY, TEAM, TEAM_NOW, TOPICS, comment,  # noqa: E402
                              contract_block, issue, summary, summary_51)

from earmark.cli import render as R  # noqa: E402


class LsTests(unittest.TestCase):
    def test_contract_sample(self):
        page = {"items": [summary(blocks_open=1), summary_51()], "total": 2, "limit": 50, "offset": 0}
        self.assertEqual(R.render_ls(page, day=DAY), contract_block("KEY      STATUS"))

    def test_footer_with_more(self):
        page = {"items": [summary()] * 50, "total": 212, "limit": 50, "offset": 0}
        self.assertEqual(R.render_ls(page, day=DAY).splitlines()[-1],
                         "50 of 212 open issues · next: --offset 50")
        page["offset"] = 50
        self.assertEqual(R.ls_footer(page), "50 of 212 open issues · offset 50 · next: --offset 100")
        page["offset"] = 200
        page["items"] = page["items"][:12]
        self.assertEqual(R.ls_footer(page), "12 of 212 open issues · offset 200")

    def test_footer_noun(self):
        self.assertEqual(R.status_word(None), "open issues")
        self.assertEqual(R.status_word("closed"), "closed issues")
        self.assertEqual(R.status_word("active"), "active issues")
        self.assertEqual(R.status_word("all"), "issues")
        self.assertEqual(R.status_word("todo,in_review"), "issues")

    def test_empty(self):
        self.assertEqual(R.render_ls({"items": [], "total": 0}), "0 of 0 open issues")

    def test_flags(self):
        s = summary(comment_count=0, due="2026-11-01", blocked=True, stale=True, children=2)
        self.assertEqual(R.issue_flags(s, DAY), ["due:11-01", "overdue", "blocked", "stale", "sub:2"])
        self.assertEqual(R.issue_flags(summary(due="2027-01-05"), DAY), ["c:3", "due:2027-01-05"])
        closed = summary(status="done", due="2026-10-01", comment_count=0)
        self.assertEqual(R.issue_flags(closed, DAY), ["due:10-01"])
        self.assertEqual(R.issue_flags(summary(comment_count=0, due=None), DAY), [])

    def test_alignment_and_no_trailing_space(self):
        page = {"items": [summary(title="x" * 150), summary_51(blocked=False)], "total": 2}
        lines = R.render_ls(page, day=DAY).splitlines()
        self.assertTrue(all(ln == ln.rstrip() for ln in lines))
        title_col = lines[0].index("TITLE")
        self.assertEqual(lines[1][title_col:title_col + 3], "xxx")
        self.assertIn("x" * 150 + "  c:3", lines[1])  # piped output never cuts
        self.assertTrue(lines[2].endswith("Mute button state"))
        self.assertTrue(all(ord(ch) < 128 for ln in lines[:-1] for ch in ln))

    def test_fields(self):
        page = {"items": [summary(), summary_51()], "total": 2}
        out = R.render_ls(page, fields=R.ls_fields("key,status,labels"), day=DAY).splitlines()
        self.assertEqual(out[0], "KEY      STATUS       LABELS")
        self.assertEqual(out[1], "GAME-42  in_progress  regression")
        self.assertEqual(out[2], "GAME-51  todo         -")
        out = R.render_ls(page, fields=R.ls_fields("key,title,flags"), day=DAY).splitlines()
        self.assertEqual(out[1], "GAME-42  Eye color widget resets on reload  c:3 due:11-07")
        with self.assertRaises(ValueError):
            R.ls_fields("key,bogus")
        self.assertEqual(R.ls_fields("prio,updated"), ["priority", "updated_at"])

    def test_terminal_width_fits_title(self):
        page = {"items": [summary(title="y" * 150)], "total": 1}
        styled = R.Style(True, "…")
        lines = R.render_ls(page, day=DAY, width=140, style=styled).splitlines()
        plain = [R_strip(ln) for ln in lines]
        self.assertLessEqual(len(plain[1]), 140)
        self.assertGreater(len(plain[1]), 120)
        self.assertIn("…", plain[1])
        self.assertIn("\x1b[", lines[1])

    def test_color_keeps_alignment(self):
        page = {"items": [summary(), summary_51()], "total": 2}
        styled = [R_strip(ln) for ln in R.render_ls(page, day=DAY, style=R.Style(True, "~"),
                                                     width=200).splitlines()]
        plain = R.render_ls(page, day=DAY, width=200).splitlines()
        self.assertEqual([len(ln) for ln in styled], [len(ln) for ln in plain])
        self.assertEqual(styled[0].index("TITLE"), plain[0].index("TITLE"))
        self.assertIn("face-la~  Eye", styled[1])
        self.assertIn("face-...  Eye", plain[1])  # a terminal without color
        piped = R.render_ls(page, day=DAY).splitlines()
        self.assertIn("ui-ux/character-menu/face-layer/eye-color  Eye", piped[1])


def R_strip(text: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class ShowTests(unittest.TestCase):
    def test_contract_sample(self):
        self.assertEqual(R.render_show(issue(), day=DAY), contract_block("GAME-42  Eye color"))

    def test_minimal_issue(self):
        out = R.render_show(issue(status="todo", assignee=None, lease_until=None, labels=[],
                                  due=None, parent=None, children=[], links={}, body="",
                                  comment_count=0, last_comment_id=None, new_comments=None,
                                  topic=None), day=DAY).splitlines()
        self.assertEqual(out[2], "topic: -")
        self.assertEqual(out[3], "assignee: -   reviewer: -   reporter: hector")
        self.assertEqual(out[4], "labels: -   due: -")
        self.assertEqual(out[5], "parent: -   sub: -   blocks: -   blocked by: -")
        self.assertEqual(out[7], "comments: 0")
        self.assertEqual(out[-1], "(no description)")

    def test_review_lease_stale_overdue_extra_links(self):
        it = issue(status="in_review", reviewer="codex-qa", stale=True, due="2026-11-01",
                   links={"blocks": [], "blocked_by": ["GAME-9"], "duplicates": ["GAME-3"],
                          "duplicated_by": [], "relates": ["GAME-5", "GAME-6"]})
        out = R.render_show(it, day=DAY).splitlines()
        self.assertEqual(out[3], "assignee: claude-ui   reviewer: codex-qa (lease until "
                                 "2026-11-03T16:05:00Z, stale)   reporter: hector")
        self.assertEqual(out[4], "labels: regression   due: 2026-11-01 (overdue)")
        self.assertIn("blocked by: GAME-9", out[5])
        self.assertEqual(out[6], "duplicates: GAME-3   duplicated by: -   relates: GAME-5, GAME-6")

    def test_body_never_cut_and_comments_appended(self):
        body = "line one\n\n" + "z" * 500
        out = R.render_show(issue(body=body, comments=COMMENTS), day=DAY)
        self.assertIn("z" * 500, out)
        self.assertIn("\nlast 2 of 12 comments:\n#1187  claude-ui  progress", out)

    def test_fields(self):
        it = issue(body="first\nsecond")
        fields = R.show_fields("status,assignee,version")
        self.assertEqual(R.render_fields(it, fields),
                         "status: in_progress\nassignee: claude-ui\nversion: 7")
        self.assertEqual(R.render_fields(it, R.show_fields("children,blocks,stale,labels,body")),
                         "children: GAME-43, GAME-44\nblocks: GAME-51\nstale: false\n"
                         "labels: regression\nbody: first\n  second")
        self.assertEqual(R.render_fields(it, ["reviewer", "due"]), "reviewer: -\ndue: 2026-11-07")
        with self.assertRaises(ValueError):
            R.show_fields("status,nope")


class CommentTests(unittest.TestCase):
    def test_contract_sample(self):
        page = {"items": COMMENTS, "last_id": 1190, "has_more": False}
        self.assertEqual(R.render_comments(page), contract_block("#1187  claude-ui"))

    def test_more_and_empty(self):
        page = {"items": COMMENTS, "last_id": 1190, "has_more": True}
        self.assertEqual(R.render_comments(page).splitlines()[-1],
                         "last: #1190 · 2 comments · more: --since 1190")
        self.assertEqual(R.render_comments(page, new=True).splitlines()[-1],
                         "last: #1190 · 2 comments · more: --new")
        empty = {"items": [], "last_id": 1190, "has_more": False}
        self.assertEqual(R.render_comments(empty, new=True), "comments: nothing new after #1190")
        self.assertEqual(R.render_comments({"items": [], "last_id": None}, since=7),
                         "comments: nothing after #7")
        self.assertEqual(R.render_comments({"items": [], "last_id": None}, key="GAME-1"),
                         "comments: none on GAME-1")

    def test_multiline_body_and_edited(self):
        c = comment(5, "a", "question", "one\n\n  two", "2026-11-03T00:00:00Z",
                    edited_at="2026-11-03T00:01:00Z")
        self.assertEqual(R.comment_lines(c), ["#5  a  question  2026-11-03T00:00:00Z  edited",
                                              "  one", "", "    two"])

    def test_inbox(self):
        page = {"items": COMMENTS, "last_id": 1190, "has_more": True, "cursor": 1190}
        out = R.render_inbox(page).splitlines()
        self.assertEqual(out[0], "GAME-42  #1187  claude-ui  progress  2026-11-03T14:05:09Z")
        self.assertEqual(out[-1], "last: #1190 · 2 comments · more: earmark inbox")
        self.assertEqual(R.render_inbox(page, peek=True).splitlines()[-1],
                         "last: #1190 · 2 comments · more: --peek --limit 4")
        self.assertEqual(R.render_inbox({"items": [], "cursor": 1190, "last_id": None}),
                         "inbox: nothing new after #1190")
        self.assertEqual(R.render_inbox({"items": [], "cursor": 0}), "inbox: nothing new")


class TopicTeamTests(unittest.TestCase):
    def test_topics_contract_sample(self):
        self.assertEqual(R.render_topics(TOPICS), contract_block("ui-ux    "))

    def test_topics_subtree_and_archived(self):
        node = dict(TOPICS[0]["children"][0], archived=True)
        out = R.render_topics([node]).splitlines()
        self.assertTrue(out[0].startswith("ui-ux/character-menu  "))
        self.assertTrue(out[0].endswith("9 open  archived"))
        self.assertTrue(out[1].startswith("  face-layer"))
        self.assertEqual(R.render_topics([]), "topics: none · add one: earmark topic add PATH")

    def test_team_contract_sample(self):
        self.assertEqual(R.render_team(TEAM, now=TEAM_NOW), contract_block("HANDLE      ROLE"))

    def test_ago(self):
        self.assertEqual(R.ago("2026-11-03T15:59:30Z", TEAM_NOW), "30s")
        self.assertEqual(R.ago("2026-10-31T16:00:00Z", TEAM_NOW), "3d")
        self.assertEqual(R.ago("2026-10-01T16:00:00Z", TEAM_NOW), "4w")
        self.assertEqual(R.ago(None, TEAM_NOW), "-")
        self.assertEqual(R.ago("garbage", TEAM_NOW), "-")

    def test_profiles(self):
        out = R.render_profiles([
            {"slug": "worker", "role": "worker", "name": "Worker", "topics": [], "agents": ["w-1"]},
            {"slug": "ui-lead", "role": "worker", "name": "UI lead", "topics": ["ui-ux"], "agents": []},
        ]).splitlines()
        self.assertEqual(out[0], "SLUG     ROLE    NAME     SCOPE        AGENTS")
        self.assertEqual(out[1], "worker   worker  Worker   whole board  w-1")
        self.assertEqual(out[2], "ui-lead  worker  UI lead  ui-ux        -")

    def test_profile_show(self):
        p = {"slug": "ui-lead", "name": "UI lead", "role": "worker", "prompt": "You are...\nline 2",
             "topics": ["ui-ux"], "version": 2, "agents": ["claude-ui"],
             "created_at": "2026-11-01T00:00:00Z", "updated_at": "2026-11-02T00:00:00Z"}
        self.assertEqual(R.render_profile(p).splitlines(), [
            "ui-lead  UI lead", "role: worker   version: 2   agents: claude-ui", "scope: ui-ux",
            "created: 2026-11-01T00:00:00Z   updated: 2026-11-02T00:00:00Z", "",
            "You are...", "line 2"])

    def test_briefing(self):
        b = {"agent": {"handle": "worker-1", "profile": "worker", "role": "worker"},
             "profile": {"slug": "worker", "role": "worker", "prompt": "You are a worker."},
             "scope": [], "assigned": [summary()], "inbox_new": 2, "loop": "1. earmark next",
             "board": {"prefix": "GAME", "require_review": True, "lease_minutes": 120}}
        out = R.render_briefing(b, day=DAY).splitlines()
        self.assertEqual(out[0], "you are worker-1 · profile worker (worker) · board GAME")
        self.assertEqual(out[1], "pass --as worker-1 on every earmark command")
        self.assertEqual(out[2], "scope: whole board")
        self.assertEqual(out[3], "review required: yes · lease: 120m")
        self.assertIn("You are a worker.", out)
        self.assertIn("assigned to you (1):", out)
        self.assertIn("inbox: 2 new", out)
        self.assertEqual(out[-1], "1. earmark next")


class MiscTests(unittest.TestCase):
    def test_status_line(self):
        self.assertEqual(R.status_line(summary(), "claimed"),
                         "GAME-42 claimed · in_progress · assignee claude-ui · lease until 2026-11-03T16:05:00Z")
        self.assertEqual(R.status_line(summary(status="todo", assignee=None, lease_until=None), "released"),
                         "GAME-42 released · todo")
        self.assertEqual(R.status_line(summary(status="done", lease_until=None), "done"), "GAME-42 done")
        self.assertEqual(R.status_line(summary(status="in_review", lease_until=None), "sent to review"),
                         "GAME-42 sent to review · in_review · assignee claude-ui")

    def test_doctor(self):
        out = R.render_doctor([{"name": "python", "ok": True, "detail": "3.12.1"},
                               {"name": "wal", "ok": False, "detail": "journal_mode=delete"}])
        self.assertEqual(out.splitlines(), ["ok    python  3.12.1", "FAIL  wal     journal_mode=delete",
                                            "doctor: 1 of 2 checks ok"])

    def test_wait(self):
        self.assertEqual(R.render_wait({"reason": "work", "key": "GAME-42", "title": "Eye color"}),
                         "wait: work · GAME-42 Eye color · next: earmark next")
        self.assertEqual(R.render_wait({"reason": "review", "issue": summary()}),
                         "wait: review · GAME-42 Eye color widget resets on reload · next: earmark next --review")
        self.assertEqual(R.render_wait({"reason": "inbox", "count": 2}),
                         "wait: inbox · 2 new · next: earmark inbox")

    def test_dash(self):
        self.assertEqual(R.dash(None), "-")
        self.assertEqual(R.dash([]), "-")
        self.assertEqual(R.dash(["a", "b"]), "a, b")
        self.assertEqual(R.dash(3072.0), "3072")
        self.assertEqual(R.dash(False), "false")

    def test_cut(self):
        self.assertEqual(R.cut("abcdef", 4), "abcd")
        self.assertEqual(R.cut("abcdef", 5, "..."), "ab...")
        self.assertEqual(R.cut("abc", 5, "..."), "abc")

    def test_kv_and_info(self):
        info = {"path": "/x/.earmark/earmark.db", "prefix": "GAME", "owner": "hector",
                "created_at": "2026-11-01T00:00:00Z", "schema_version": 1,
                "settings": {"require_review": True, "lease_minutes": 120},
                "counts": {"by_status": {"todo": 2, "done": 1}, "open": 2, "total": 3},
                "agents": {"online": 2, "total": 3}, "topics": 5, "heads": {"event_id": 9},
                "files": {"db": 4096, "wal": 0}, "enums": {"statuses": ["todo"]},
                "version": "0.1.0", "contract_version": "1.0.0", "sqlite_version": "3.46.1"}
        self.assertEqual(R.render_info(info).splitlines(), [
            "board: /x/.earmark/earmark.db",
            "prefix: GAME · owner: hector · schema: 1 · created: 2026-11-01T00:00:00Z",
            "settings: require_review true · lease_minutes 120",
            "issues: 3 total, 2 open · todo 2 · done 1", "agents: 2 online of 3 · topics: 5",
            "files: db 4.0 KB · wal 0 B", "versions: earmark 0.1.0 · contract 1.0.0 · sqlite 3.46.1"])
        self.assertEqual(R.render_kv({"require_review": True, "lease_minutes": 120}),
                         "require_review: true\nlease_minutes: 120")

    def test_triage(self):
        res = {"backlog": [], "unassigned": [summary_51(blocked=False)],
               "stale": [summary(stale=True, comment_count=0, due=None)],
               "review_queue": [summary(status="in_review", lease_until=None)],
               "review_since": {"GAME-42": "2026-11-03T14:00:00Z"}, "blocked": [], "overdue": [],
               "idle_agents": [{"handle": "grok-pm", "role": "worker", "profile": "worker", "cli": "grok",
                                "presence": "idle", "last_seen": "2026-11-03T15:00:00Z"}],
               "counts": {"backlog": 0, "unassigned": 1, "stale": 1, "review_queue": 1, "blocked": 0,
                          "overdue": 0, "idle_agents": 1}}
        out = R.render_triage(res, day=DAY, now=TEAM_NOW).splitlines()
        self.assertEqual(out[0], "triage: untriaged backlog 0 · unassigned todo 1 · stale claims 1 · "
                                 "review queue 1 · blocked 0 · overdue 0 · idle agents 1")
        self.assertEqual(out[1:5], ["", "unassigned todo (1):",  # status and assignee go without saying
                                    "KEY      PRIO    TYPE  TOPIC              TITLE",
                                    "GAME-51  medium  task  ui-ux/video-calls  Mute button state"])
        self.assertEqual(out[6], "stale claims (1):")
        self.assertEqual(out[7].split()[:7], ["KEY", "STATUS", "PRIO", "TYPE", "ASSIGNEE", "LEASE", "TOPIC"])
        self.assertTrue(out[8].endswith("Eye color widget resets on reload  stale"))
        self.assertEqual(out[10], "review queue (1):")
        self.assertEqual(out[11].split(), ["KEY", "PRIO", "TYPE", "ASSIGNEE", "WAITING", "TOPIC", "TITLE"])
        self.assertEqual(out[12].split()[:5], ["GAME-42", "high", "bug", "claude-ui", "2h"])
        self.assertEqual(out[14:], ["idle agents (1):", "HANDLE   ROLE    PROFILE  CLI   SEEN  STATE",
                                    "grok-pm  worker  worker   grok  1h    idle"])
        self.assertFalse(any("=" in ln for ln in out))
        empty = {k: [] for k in ("backlog", "unassigned", "stale", "review_queue", "blocked",
                                 "overdue", "idle_agents")}
        self.assertEqual(R.render_triage(empty).splitlines()[-1], "nothing to triage")

    def test_triage_cut_sections_name_the_full_call(self):
        rows = [summary(key=f"GAME-{n}", id=n, status="todo", assignee=None, priority="high",
                        topic=f"t{n % 2}", comment_count=0, due=None) for n in range(1, 11)]
        res = {"unassigned": rows, "stale": rows[:2], "overdue": rows[:1], "blocked": rows[:3],
               "backlog": rows[:2], "review_queue": [],
               "idle_agents": [{"handle": "a", "presence": "idle"}],
               "counts": {"unassigned": 14, "stale": 5, "overdue": 1, "blocked": 4, "backlog": 9,
                          "review_queue": 0, "idle_agents": 3}}
        out = R.render_triage(res, day=DAY).splitlines()
        more = [ln for ln in out if ln.startswith("+")]
        self.assertEqual(more, ["+7 more: earmark ls -s backlog", "+4 more: earmark ls -s todo -a none",
                                "+3 more: earmark ls --stale", "+1 more: earmark ls --blocked",
                                "+2 more: earmark team"])
        header = out[out.index("unassigned todo (14):") + 1]
        self.assertEqual(header.split(), ["KEY", "TOPIC", "TITLE"])  # status, prio, type, assignee: same on all
        self.assertEqual(out[out.index("overdue (1):") + 1].split(),
                         ["KEY", "STATUS", "PRIO", "TYPE", "ASSIGNEE", "TOPIC", "TITLE"])


if __name__ == "__main__":
    unittest.main()
