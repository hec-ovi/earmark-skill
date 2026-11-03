"""Topic tree: ancestors, rolled-up counts, depth/root/archive views, edit, move, remove."""

from __future__ import annotations

import unittest

from tests.support import BoardCase

from earmark.errors import Conflict, InvalidRequest, NotFound

TOPIC_KEYS = {"id", "path", "slug", "name", "icon", "description", "parent_id", "position",
              "archived", "counts", "children"}


def paths(nodes) -> list[str]:
    out = []
    for node in nodes:
        out.append(node["path"])
        out.extend(paths(node["children"]))
    return out


class Tree(BoardCase):
    def test_add_creates_ancestors(self):
        leaf = self.board.add_topic("ui-ux/character-menu/face-layer", name="Face layer",
                                    icon="smile", description="Face customization")
        self.assertEqual(set(leaf), TOPIC_KEYS)
        self.assertEqual((leaf["slug"], leaf["name"], leaf["icon"], leaf["description"]),
                         ("face-layer", "Face layer", "smile", "Face customization"))
        tree = self.board.topics()
        self.assertEqual(paths(tree), ["ui-ux", "ui-ux/character-menu", "ui-ux/character-menu/face-layer"])
        self.assertEqual((tree[0]["name"], tree[0]["parent_id"]), ("ui-ux", None))
        self.assertEqual(tree[0]["children"][0]["parent_id"], tree[0]["id"])
        added = [e["new"] for e in self.board.activity()["items"] if e["action"] == "topic_add"]
        self.assertEqual(len(added), 3)
        with self.assertRaises(Conflict):
            self.board.add_topic("ui-ux/character-menu")
        sibling = self.board.add_topic("ui-ux/video-calls")
        self.assertEqual(sibling["position"], 1)
        with self.assertRaises(InvalidRequest):
            self.board.add_topic("Bad Path!")

    def test_counts_roll_up(self):
        b = self.board
        b.add_topic("ui-ux/menu/face")
        b.add_topic("ui-ux/calls")
        self.issue("a", topic="ui-ux/menu/face")
        self.issue("b", topic="ui-ux/menu/face")
        self.issue("c", topic="ui-ux/menu")
        self.issue("d", topic="ui-ux/calls")
        b.cancel(self.issue("e", topic="ui-ux/calls")["key"], note="no")
        self.issue("f")
        root = b.topics()[0]
        self.assertEqual((root["counts"]["open"], root["counts"]["total"]), (4, 5))
        self.assertEqual(root["counts"]["by_status"]["canceled"], 1)
        self.assertEqual(root["counts"]["by_status"]["todo"], 4)
        menu = root["children"][0]
        self.assertEqual((menu["path"], menu["counts"]["open"]), ("ui-ux/menu", 3))
        self.assertEqual(menu["children"][0]["counts"]["total"], 2)
        self.assertEqual(root["children"][1]["counts"]["open"], 1)

    def test_root_depth_and_archive(self):
        b = self.board
        b.add_topic("a/b/c")
        b.add_topic("a/x")
        b.add_topic("z")
        self.assertEqual(paths(b.topics(depth=1)), ["a", "z"])
        self.assertEqual(paths(b.topics(depth=2)), ["a", "a/b", "a/x", "z"])
        self.assertEqual(paths(b.topics(root="a/b")), ["a/b", "a/b/c"])
        self.assertEqual(paths(b.topics(root="a", depth=2)), ["a", "a/b", "a/x"])
        archived = b.edit_topic("a/b", archived=True)
        self.assertTrue(archived["archived"])
        self.assertEqual(paths(b.topics()), ["a", "a/x", "z"])
        self.assertEqual(paths(b.topics(include_archived=True)), ["a", "a/b", "a/b/c", "a/x", "z"])
        with self.assertRaises(NotFound):
            b.topics(root="nope")
        with self.assertRaises(InvalidRequest):
            b.topics(depth=0)


class Edit(BoardCase):
    def test_fields_and_refs(self):
        topic = self.board.add_topic("ui-ux")
        edited = self.board.edit_topic(topic["id"], name="UI & UX", icon="palette",
                                       description="All screens", position=5)
        self.assertEqual((edited["name"], edited["icon"], edited["description"], edited["position"]),
                         ("UI & UX", "palette", "All screens", 5))
        self.assertIsNone(self.board.edit_topic("ui-ux", icon="")["icon"])
        self.assertEqual(self.board.edit_topic(str(topic["id"]), name="UI")["name"], "UI")
        with self.assertRaises(InvalidRequest):
            self.board.edit_topic("ui-ux", name="  ")
        with self.assertRaises(NotFound):
            self.board.edit_topic(999, name="x")
        actions = [e["action"] for e in self.board.activity()["items"]]
        self.assertGreaterEqual(actions.count("topic_edit"), 5)


class Move(BoardCase):
    def test_rewrites_the_subtree_and_issues_follow(self):
        b = self.board
        b.add_topic("ui-ux/character-menu/face-layer/eye-color")
        b.add_topic("ui-ux/other")
        key = self.issue("eye", topic="ui-ux/character-menu/face-layer/eye-color")["key"]
        b.add_profile("face", name="Face", role="worker", topics=["ui-ux/character-menu/face-layer"])
        moved = b.move_topic("ui-ux/character-menu", "game/menus/character")
        self.assertEqual((moved["path"], moved["slug"]), ("game/menus/character", "character"))
        self.assertEqual(b.get_issue(key)["topic"], "game/menus/character/face-layer/eye-color")
        self.assertEqual(b.get_profile("face")["topics"], ["game/menus/character/face-layer"])
        self.assertEqual(paths(b.topics()), ["ui-ux", "ui-ux/other", "game", "game/menus",
                                             "game/menus/character", "game/menus/character/face-layer",
                                             "game/menus/character/face-layer/eye-color"])
        self.assertEqual(b.list_issues(topic=["game/menus"])["total"], 1)
        renamed = b.move_topic("ui-ux/other", "ui-ux/renamed")
        self.assertEqual(renamed["parent_id"], b.topics()[0]["id"])
        self.assertEqual(b.activity()["items"][0]["action"], "topic_move")

    def test_rules(self):
        b = self.board
        b.add_topic("a/b")
        b.add_topic("c")
        with self.assertRaises(InvalidRequest):
            b.move_topic("a", "a/b/inside")
        with self.assertRaises(Conflict):
            b.move_topic("a/b", "c")
        self.assertEqual(b.move_topic("a/b", "a/b")["path"], "a/b")
        b.add_topic("/".join(["d"] * 15))
        with self.assertRaises(InvalidRequest):
            b.move_topic("a", "/".join(["e"] * 16))


class Remove(BoardCase):
    def test_empty_subtree_only_unless_archive(self):
        b = self.board
        b.add_topic("a/b")
        b.add_topic("solo")
        b.add_profile("solo-team", name="Solo", role="worker", topics=["solo"])
        self.issue("x", topic="a/b")
        with self.assertRaises(InvalidRequest) as ctx:
            b.remove_topic("a")
        self.assertEqual(ctx.exception.details, {"children": 1, "issues": 1})
        with self.assertRaises(InvalidRequest):
            b.remove_topic("a/b")
        self.assertEqual(b.remove_topic("a", archive=True)["archived"], b.topics(include_archived=True)[0]["id"])
        self.assertEqual(paths(b.topics()), ["solo"])
        removed = b.remove_topic("solo")
        self.assertEqual(removed["path"], "solo")
        self.assertEqual(b.get_profile("solo-team")["topics"], [])
        with self.assertRaises(NotFound):
            b.remove_topic("solo")


if __name__ == "__main__":
    unittest.main()
