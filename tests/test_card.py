#!/usr/bin/env python3
"""Message Card 中间表示 + EVENT_PALETTE + 各渠道渲染层单测。

覆盖：
- EVENT_PALETTE 查表（包括 workflow_run 的 green/red 二级映射）
- assemble_card 字段规则（watch/fork 不带 actor；其他事件带 actor/branch/labels/commit/wf_status/comment）
- Card 渲染到飞书 / Slack / 通用 Webhook（按 fixtures/example_events.json）
- channel_test(name, card) 与旧默认行为
- channel_test_event(name, event) 示例事件推送（配色 / 字段整张下发、不写日志）
- Bark / PushDeer / wecom 的 payload 形态（前两者丢弃 fields，wecom 只发纯文本）
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 在 import server 之前注入测试凭据（unittest discover 不走 conftest.py；
# pytest 会先加载 conftest.py 走同样的路径。test_card 字母序在前，必须显式 import）
import tests._env  # noqa: E402,F401
import server  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                        "example_events.json")


def _load_fixtures():
    with open(FIXTURES, encoding="utf-8") as f:
        return {e["key"]: e for e in json.load(f)["events"]}


FIXTURES_DATA = _load_fixtures()


class EventPaletteTest(unittest.TestCase):
    """EVENT_PALETTE 查表：单一映射，不开放用户配置（§4 拍板）。"""

    def test_known_events_have_palette_row(self):
        for key in ("watch", "fork", "issues", "issue_comment", "pull_request",
                    "release", "generic", "test"):
            _name, label, template, hex_color = server._event_palette_row(key)
            self.assertTrue(label, f"{key} label 空")
            self.assertTrue(template, f"{key} template 空")
            self.assertTrue(hex_color, f"{key} hex_color 空")
            self.assertTrue(hex_color.startswith("#"), f"{key} hex 格式: {hex_color!r}")

    def test_unknown_event_falls_back(self):
        _name, label, template, hex_color = server._event_palette_row("nonsense")
        _def_name, def_label, def_t, def_h = server.EVENT_PALETTE_DEFAULT
        self.assertEqual((label, template, hex_color), (def_label, def_t, def_h))

    def test_workflow_run_without_color_falls_back(self):
        # workflow_run 没 color 时走默认（不强行配 red）—— 兜底
        _name, label, template, hex_color = server._event_palette_row("workflow_run")
        # 没 color → EVENT_PALETTE 行（template/hex_color 都是 None），调用方应用默认值
        self.assertIsNone(template)
        self.assertIsNone(hex_color)

    def test_workflow_run_picks_color(self):
        for color, exp_hex, exp_template, exp_label in [
            ("green", "#2da44e", "green", "通过"),
            ("red",   "#cf222e", "red",   "失败"),
        ]:
            _name, label, template, hex_color = server._event_palette_row(
                "workflow_run", color)
            self.assertEqual(hex_color, exp_hex, color)
            self.assertEqual(template, exp_template, color)
            self.assertEqual(label, exp_label, color)

    def test_palette_has_no_user_overrides(self):
        """调色板是 server 常量，配置层没有覆盖入口（§4 拍板）。"""
        # 不暴露任何 setter；引用计数稳定
        self.assertIsInstance(server.EVENT_PALETTE, dict)
        self.assertEqual(set(server.EVENT_PALETTE.keys()),
                         {"watch", "fork", "issues", "issue_comment",
                          "pull_request", "workflow_run", "release",
                          "generic", "test"})


class AssembleCardTest(unittest.TestCase):
    """assemble_card：从 (title, body, meta) 装 Card，字段去冗余（§3 + §5 拍板）。"""

    def test_watch_omits_actor_field(self):
        # watch 的 actor 已写在 title「⭐ X star 了 Y」，不进 fields
        card = server.assemble_card("⭐ h star 了 bluebird", "正文",
                                    {"event": "watch", "repo": "bluebird",
                                     "actor": "h"})
        field_labels = [lbl for lbl, _ in card.fields]
        self.assertNotIn("提交人", field_labels)
        self.assertIn("仓库", field_labels)
        self.assertEqual(card.accent, "watch")

    def test_fork_omits_actor_field(self):
        card = server.assemble_card("🍴 f fork 了 bluebird", "正文",
                                    {"event": "fork", "repo": "bluebird",
                                     "actor": "f"})
        field_labels = [lbl for lbl, _ in card.fields]
        self.assertNotIn("提交人", field_labels)

    def test_pr_keeps_actor_field(self):
        # PR 没把 actor 写进 title → fields 仍带「提交人」
        card = server.assemble_card("🔀 新 PR #1", "正文",
                                    {"event": "pull_request",
                                     "repo": "bluebird", "actor": "alice",
                                     "branch": "feat/x"})
        field_labels = [lbl for lbl, _ in card.fields]
        self.assertEqual(field_labels, ["仓库", "提交人", "分支"])

    def test_workflow_run_carries_actor_branch_status(self):
        card = server.assemble_card("✅ CI 通过：x", "正文",
                                    {"event": "workflow_run", "repo": "bluebird",
                                     "actor": "dave", "branch": "main",
                                     "wf_status": "通过", "color": "green"})
        field_labels = [lbl for lbl, _ in card.fields]
        self.assertIn("提交人", field_labels)
        self.assertIn("分支", field_labels)
        self.assertIn("状态", field_labels)
        self.assertEqual(card.accent, "workflow_run")
        self.assertEqual(card.accent_color, "green")

    def test_issue_keeps_labels(self):
        card = server.assemble_card("🐛 新 issue", "正文",
                                    {"event": "issues", "repo": "bluebird",
                                     "actor": "alice", "labels": "bug, ui"})
        field_labels = [lbl for lbl, _ in card.fields]
        self.assertIn("标签", field_labels)

    def test_issue_comment_keeps_comment(self):
        card = server.assemble_card("💬 评论", "正文",
                                    {"event": "issue_comment", "repo": "bluebird",
                                     "actor": "bob",
                                     "comment": "评论内容"})
        field_labels = [lbl for lbl, _ in card.fields]
        self.assertIn("评论摘录", field_labels)

    def test_default_event_is_generic(self):
        card = server.assemble_card("通知", "正文", {})
        self.assertEqual(card.accent, "generic")

    def test_action_url_and_source_passthrough(self):
        card = server.assemble_card("t", "b", {"event": "issues",
                                                "url": "https://x",
                                                "source": "demo"})
        self.assertEqual(card.action_url, "https://x")
        self.assertEqual(card.source, "demo")


class FeishuRenderTest(unittest.TestCase):
    """按 fixture 渲染飞书卡片：header.template 与 events 一一对应。"""

    def _render(self, key):
        e = FIXTURES_DATA[key]
        card = server.assemble_card(e["title"], e["body"], e["meta"])
        return server._feishu_card(card)

    def test_watch_header_yellow(self):
        out = self._render("watch")
        self.assertEqual(out["header"]["template"], "yellow")
        self.assertEqual(out["header"]["title"]["content"], "⭐ hancic star 了 bluebird")

    def test_fork_header_purple(self):
        out = self._render("fork")
        self.assertEqual(out["header"]["template"], "purple")

    def test_issue_header_orange(self):
        out = self._render("issues")
        self.assertEqual(out["header"]["template"], "orange")

    def test_comment_header_turquoise(self):
        out = self._render("issue_comment")
        self.assertEqual(out["header"]["template"], "turquoise")

    def test_pr_header_blue(self):
        out = self._render("pull_request")
        self.assertEqual(out["header"]["template"], "blue")

    def test_workflow_run_pass_header_green(self):
        out = self._render("workflow_run_pass")
        self.assertEqual(out["header"]["template"], "green")

    def test_workflow_run_fail_header_red(self):
        out = self._render("workflow_run_fail")
        self.assertEqual(out["header"]["template"], "red")

    def test_release_header_green(self):
        out = self._render("release")
        self.assertEqual(out["header"]["template"], "green")

    def test_watch_does_not_render_actor_field(self):
        out = self._render("watch")
        field_elements = [e for e in out["elements"] if e["tag"] == "div"]
        self.assertTrue(field_elements)
        labels = [f["text"]["content"].splitlines()[0]
                  for f in field_elements[0]["fields"]]
        self.assertNotIn("**提交人**", labels)
        self.assertIn("**仓库**", labels)


class SlackRenderTest(unittest.TestCase):
    """Slack 渲染：attachment.color 与 events 对应。"""

    def _render(self, key):
        e = FIXTURES_DATA[key]
        card = server.assemble_card(e["title"], e["body"], e["meta"])
        return server._slack_payload(card)

    def test_watch_color(self):
        self.assertEqual(self._render("watch")["attachments"][0]["color"], "#dfac03")

    def test_workflow_run_pass_color_green(self):
        self.assertEqual(self._render("workflow_run_pass")["attachments"][0]["color"], "#2da44e")

    def test_workflow_run_fail_color_red(self):
        self.assertEqual(self._render("workflow_run_fail")["attachments"][0]["color"], "#cf222e")

    def test_text_fallback_contains_title(self):
        payload = self._render("pull_request")
        self.assertIn("新 PR #7", payload["text"])

    def test_header_uses_emoji(self):
        payload = self._render("watch")
        header_block = payload["blocks"][0]
        self.assertEqual(header_block["type"], "header")
        self.assertIn("⭐", header_block["text"]["text"])
        self.assertIn("hancic", header_block["text"]["text"])


class WebhookPayloadTest(unittest.TestCase):
    """通用 Webhook payload：§6.5 字段序固定（title/body/accent/fields/action_url/source/timestamp）。"""

    def _render(self, key, cfg, post_mock):
        e = FIXTURES_DATA[key]
        card = server.assemble_card(e["title"], e["body"], e["meta"])
        card.source = e["meta"].get("source", "github")
        card.timestamp = 1700000000
        self.assertTrue(server._do_push("webhook", card, cfg))
        url, body, headers = post_mock.call_args[0]
        return url, json.loads(body.decode("utf-8")), headers

    def test_field_order_is_stable(self):
        from unittest import mock
        with mock.patch("server._post_raw", return_value=True) as post:
            url, payload, _ = self._render(
                "pull_request", {"url": "https://hook.example.com/x"}, post)
            keys = list(payload.keys())
            # §6.5 固定序：title / body / accent / fields / action_url / source / timestamp
            self.assertEqual(keys,
                             ["title", "body", "accent", "fields",
                              "action_url", "source", "timestamp"])

    def test_fields_serialized_as_label_value_objects(self):
        from unittest import mock
        with mock.patch("server._post_raw", return_value=True) as post:
            _, payload, _ = self._render(
                "pull_request", {"url": "https://hook.example.com/x"}, post)
            self.assertIsInstance(payload["fields"], list)
            for f in payload["fields"]:
                self.assertIn("label", f)
                self.assertIn("value", f)
            labels = [f["label"] for f in payload["fields"]]
            # fixture 里有 labels + commit → fields 包含它们
            self.assertEqual(labels, ["仓库", "提交人", "分支", "标签", "commit"])

    def test_accent_named_for_event(self):
        from unittest import mock
        with mock.patch("server._post_raw", return_value=True) as post:
            _, payload, _ = self._render(
                "issues", {"url": "https://h/x"}, post)
            self.assertEqual(payload["accent"], "issue")

    def test_workflow_run_accent_is_ci_pass(self):
        from unittest import mock
        with mock.patch("server._post_raw", return_value=True) as post:
            _, payload, _ = self._render(
                "workflow_run_pass", {"url": "https://h/x"}, post)
            self.assertEqual(payload["accent"], "ci_pass")

    def test_workflow_run_fail_accent_is_ci_fail(self):
        from unittest import mock
        with mock.patch("server._post_raw", return_value=True) as post:
            _, payload, _ = self._render(
                "workflow_run_fail", {"url": "https://h/x"}, post)
            self.assertEqual(payload["accent"], "ci_fail")

    def test_signature_uses_serialized_payload(self):
        import hashlib, hmac
        from unittest import mock
        with mock.patch("server._post_raw", return_value=True) as post:
            e = FIXTURES_DATA["pull_request"]
            card = server.assemble_card(e["title"], e["body"], e["meta"])
            card.source = "github"
            card.timestamp = 1700000000
            self.assertTrue(server._do_push("webhook", card,
                                           {"url": "https://h/x", "secret": "s3cret"}))
            body, headers = post.call_args[0][1], post.call_args[0][2]
            expect = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
            self.assertEqual(headers["X-Bluebird-Signature-256"], expect)


class ChannelTestEventTest(unittest.TestCase):
    """channel_test(name, card)：示例事件推送入口（§10 拍板）。"""

    def setUp(self):
        from unittest import mock
        self._channels = [
            {"name": "bark", "type": "bark", "config": {"key": "k"}},
        ]
        self._orig = server.get_channels
        server.get_channels = lambda: self._channels

    def tearDown(self):
        server.get_channels = self._orig

    def test_default_uses_test_event(self):
        from unittest import mock
        with mock.patch("server._push_channel", return_value=True) as push:
            ok, err = server.channel_test("bark")
            self.assertTrue(ok, err)
            # 旧 4 参：typ, title, body, cfg, source
            args = push.call_args[0]
            self.assertEqual(args[0], "bark")
            self.assertIn("测试推送", args[1])
            self.assertEqual(args[4], "test")

    def test_passed_card_routes_as_card(self):
        from unittest import mock
        card = server.assemble_card("🚀 新发布：v0.0.11", "正文",
                                    {"event": "release", "source": "test"})
        with mock.patch("server._push_channel", return_value=True) as push:
            ok, err = server.channel_test("bark", card)
            self.assertTrue(ok, err)
            # 新 2 参形态：typ, card, cfg —— 整张下发，accent / fields / action_url 不丢
            args = push.call_args[0]
            self.assertEqual(args[0], "bark")
            self.assertIs(args[1], card)
            self.assertEqual(args[1].accent, "release")


class ExampleEventPushTest(unittest.TestCase):
    """channel_test_event(name, event)：按预置 fixture 整张 Card 真实下发（设计稿 §7 / §10）。"""

    def setUp(self):
        self._channels = [
            {"name": "bark", "type": "bark", "config": {"key": "k"}},
            {"name": "wecom", "type": "wecom", "config": {"webhook": "https://w"}},
        ]
        self._orig = server.get_channels
        server.get_channels = lambda: self._channels

    def tearDown(self):
        server.get_channels = self._orig

    def test_fixture_keys_loaded(self):
        keys = set(server.example_events())
        self.assertTrue({"watch", "fork", "issues", "issue_comment", "pull_request",
                         "workflow_run_pass", "workflow_run_fail", "release"} <= keys, keys)

    def test_options_carry_chinese_labels(self):
        opts = {o["key"]: o["label"] for o in server.example_event_options()}
        self.assertEqual(opts["watch"], "新 Star")
        self.assertEqual(opts["workflow_run_pass"], "CI 通过")
        self.assertEqual(opts["workflow_run_fail"], "CI 失败")
        self.assertEqual(opts["release"], "新发布")

    def test_unknown_event_rejected(self):
        ok, err = server.channel_test_event("bark", "no-such-event")
        self.assertFalse(ok)
        self.assertIn("未知示例事件", err)

    def test_workflow_run_failure_end_to_end_bark(self):
        """端到端：示例事件 → 真实 payload（Bark 只吃 title/body，不带 fields）。"""
        from unittest import mock
        with mock.patch("server._post_json", return_value={"code": 200}) as post:
            # 兼容设计稿的 workflow_run_failure 写法（夹具 key 是 workflow_run_fail）
            ok, err = server.channel_test_event("bark", "workflow_run_failure")
            self.assertTrue(ok, err)
            url, payload = post.call_args[0]
            self.assertTrue(url.endswith("/k"), url)
            self.assertIn("CI 失败", payload["title"])
            self.assertEqual(set(payload), {"title", "body", "subtitle", "group", "sound"})
            self.assertNotIn("fields", payload)

    def test_card_fidelity_through_push_channel(self):
        from unittest import mock
        with mock.patch("server._do_push", return_value=True) as dp:
            ok, err = server.channel_test_event("wecom", "pull_request")
            self.assertTrue(ok, err)
            typ, card, cfg = dp.call_args[0]
            self.assertEqual(typ, "wecom")
            self.assertEqual(card.accent, "pull_request")
            self.assertEqual(card.source, "test")
            self.assertIn(("标签", "enhancement"), card.fields)
            self.assertTrue(card.action_url.startswith("https://github.com/"))

    def test_does_not_write_push_log(self):
        from unittest import mock
        with mock.patch("server._do_push", return_value=True):
            server.channel_test_event("bark", "watch")
        self.assertEqual(server.count_logs(days=1)["total"], 0)


class ChannelPayloadShapeTest(unittest.TestCase):
    """Bark / PushDeer 丢弃 fields；wecom 只发纯文本，不引入卡片（设计稿 §6.3 / §6.4）。"""

    def _card(self):
        return server.assemble_card(
            "标题", "正文",
            {"event": "pull_request", "repo": "bluebird", "actor": "carol",
             "branch": "feat/wecom", "labels": "enhancement",
             "url": "https://github.com/hancic/bluebird/pull/7"})

    def test_bark_ignores_fields(self):
        from unittest import mock
        with mock.patch("server._post_json", return_value={"code": 200}) as post:
            self.assertTrue(server._do_push("bark", self._card(), {"key": "k"}))
            payload = post.call_args[0][1]
            self.assertEqual(set(payload), {"title", "body", "subtitle", "group", "sound"})
            self.assertEqual(payload["title"], "标题")
            self.assertEqual(payload["body"], "正文")

    def test_pushdeer_ignores_fields(self):
        from unittest import mock
        with mock.patch("server._post_form") as post:
            post.return_value = {"code": 0}
            self.assertTrue(server._do_push("pushdeer", self._card(), {"key": "k"}))
            payload = post.call_args[0][1]
            self.assertEqual(set(payload), {"pushkey", "text", "desp", "type"})
            self.assertEqual(payload["text"], "标题")
            self.assertEqual(payload["desp"], "正文")

    def test_wecom_stays_plain_text(self):
        from unittest import mock
        with mock.patch("server._post_json", return_value={"errcode": 0}) as post:
            self.assertTrue(server._do_push("wecom", self._card(), {"webhook": "https://w"}))
            url, payload = post.call_args[0]
            self.assertEqual(url, "https://w")
            self.assertEqual(payload, {"msgtype": "text", "text": {"content": "标题\n正文"}})


class LegacyPushChannelShimTest(unittest.TestCase):
    """_push_channel 旧 4 参调用仍兼容（阶段 A 兼容层，不破坏既有调用方）。"""

    def test_old_signature_still_routes(self):
        from unittest import mock
        with mock.patch("server._do_push", return_value=True) as dp:
            ok = server._push_channel("bark", "t", "b",
                                      {"key": "k"}, "src", {"event": "watch"})
            self.assertTrue(ok)
            # _do_push 收到 (typ, card, cfg)
            args = dp.call_args[0]
            self.assertEqual(args[0], "bark")
            self.assertIsInstance(args[1], server.Card)
            self.assertEqual(args[1].title, "t")
            self.assertEqual(args[1].source, "src")
            self.assertEqual(args[1].accent, "watch")
            self.assertEqual(args[2], {"key": "k"})

    def test_new_signature_routes_directly(self):
        from unittest import mock
        card = server.assemble_card("t", "b", {"event": "issues"})
        with mock.patch("server._do_push", return_value=True) as dp:
            ok = server._push_channel("feishu", card, {"app_id": "x"})
            self.assertTrue(ok)
            args = dp.call_args[0]
            self.assertEqual(args[0], "feishu")
            self.assertIs(args[1], card)


if __name__ == "__main__":
    unittest.main()