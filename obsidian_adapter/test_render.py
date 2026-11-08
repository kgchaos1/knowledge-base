"""Standard-library renderer contract tests: python -m unittest obsidian_adapter.test_render."""
import copy
from datetime import datetime, timezone
import json
import re
import unittest
from unittest.mock import patch

from obsidian_adapter.render import render_note, stable_path


HASH = "0123456789abcdef" * 4


class RenderTests(unittest.TestCase):
    def note(self, **changes):
        note = dict(id=17, title="中文笔记 📝", source="zhihu",
                    source_url="https://example.com/article", tags=["知识 管理", "中文/阅读"],
                    ai_summary="【核心结论】\n**保留重点**\n\n- 第一项", content="第一段\n\n第二段",
                    content_status="full", ai_stale=0, updated_at="2026-11-06T10:00:00Z",
                    images=[], image_total=0, image_failed=0, content_warning=None)
        note.update(changes)
        return note

    def metadata(self, rendered):
        self.assertTrue(rendered.startswith("---\n"))
        header = rendered.split("\n---\n", 1)[0].splitlines()[1:]
        return {line.split(": ", 1)[0]: json.loads(line.split(": ", 1)[1]) for line in header}

    def test_stable_id_path_ignores_title(self):
        self.assertEqual(stable_path(self.note()), "笔记/pkm-17.md")
        self.assertEqual(stable_path(self.note(title="../../危险: / * ?")), "笔记/pkm-17.md")
        for invalid in (None, True, False, 0, -1, "17", "../17", 17.0, [], {}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                stable_path(self.note(id=invalid))
            with self.assertRaises(ValueError):
                render_note(self.note(id=invalid))
        with self.assertRaises(ValueError):
            stable_path({})

    def test_current_api_fields_and_utf8_markdown(self):
        rendered = render_note(self.note())
        self.assertIn("# 中文笔记 📝", rendered)
        self.assertIn("> ### 核心结论\n> **保留重点**\n>\n> - 第一项", rendered)
        self.assertIn("> [!abstract]+ AI 摘要", rendered)
        self.assertIn("> [!quote]- 原文\n> 第一段\n>\n> 第二段", rendered)
        self.assertIn("原文链接：<https://example.com/article>", rendered)
        self.assertEqual(self.metadata(rendered), dict(
            pkm_id=17, source="zhihu", source_url="https://example.com/article",
            aliases=["中文笔记 📝"], tags=["知识-管理", "中文/阅读"], content_status="full",
            ai_stale=False, updated_at="2026-11-06T10:00:00Z"))
        self.assertEqual(rendered.encode("utf-8").decode("utf-8"), rendered)

    def test_frontmatter_injection_is_json_escaped(self):
        attack = '标题"\n---\ntags: [恶意]\n---\n<script>\x00\u0085\u2028\u2029'
        rendered = render_note(self.note(title=attack, source=attack, source_url=attack,
                                         updated_at=attack, content_status=attack))
        data = self.metadata(rendered)
        self.assertEqual(data["aliases"], [attack])
        self.assertEqual(data["source"], attack)
        self.assertEqual(data["updated_at"], attack)
        self.assertEqual(rendered.splitlines().count("---"), 2)
        heading = rendered.split("\n---\n", 1)[1].splitlines()[1]
        self.assertTrue(heading.startswith("# "))
        self.assertNotIn("<script>", heading)
        self.assertNotIn("\x00", rendered)

    def test_tags_are_conservative_and_deduplicated(self):
        tags = ["a b", "a\tb", "中文/研究", "#bad", "a:b", "a.b", "a//b", "/a", "a/",
                "123", "tag_1", "a-b", "标签😀", None, 3, " ", "<img>", "a\\b"]
        rendered = render_note(self.note(tags=tags))
        self.assertEqual(self.metadata(rendered)["tags"], ["a-b", "中文/研究", "tag_1"])

    def test_local_hash_images_only_and_deduplicated(self):
        filename = HASH + ".png"
        invalid = ["../" + filename, "附件/" + filename, "/tmp/" + filename,
                   HASH + ".svg", HASH.upper() + ".png", "short.png", HASH + ".png?x=1",
                   HASH + ".png]]\n<script>"]
        images = [dict(path=filename, url="https://remote.example/image.png", alt="evil]]"),
                  dict(path=filename)] + [dict(path=x) for x in invalid] + ["https://example.com/a.png", {}]
        rendered = render_note(self.note(images=images))
        self.assertEqual(rendered.count("![[附件/" + filename + "]]"), 1)
        self.assertEqual(re.findall(r"!\[\[.*?\]\]", rendered), ["![[附件/" + filename + "]]"])
        self.assertNotIn("remote.example", rendered)
        self.assertIn("警告", rendered)
        for ext in ("jpg", "jpeg", "gif", "webp"):
            self.assertIn("![[附件/" + HASH + "." + ext + "]]",
                          render_note(self.note(images=[dict(path=HASH + "." + ext)])))

    def test_images_html_embeds_and_active_links_inert(self):
        payload = ('![remote](https://tracker.invalid/a.png)\n![ref][img]\n'
                   '[img]: https://tracker.invalid/b.png\n![[evil]]\n'
                   '[run](javascript:alert(1))\n<iframe src="https://evil"></iframe>\n'
                   '<img src="https://evil/x">\n[[internal]]\n\\![escape](https://evil)\n'
                   '```dataviewjs\nfetch("https://evil")\n```\n`=this.file.name`\n'
                   '- item\n    ```dataviewjs\n    evil()\n    ```')
        rendered = render_note(self.note(content=payload, ai_summary=payload))
        for active in ("![", "[[", "<iframe", "<img", "[run]", "```dataviewjs", "`=this"):
            self.assertNotIn(active, rendered)
        self.assertIn("```text", rendered)
        self.assertIn("&lt;iframe", rendered)
        self.assertIn("警告", rendered)

    def test_source_links_reject_unsafe_and_encode_breakouts(self):
        for url in ("javascript:alert(1)", "file:///etc/passwd", "obsidian://open", "//example.com/x",
                    "https://user@example.com/x", "https://user:pass@example.com", "https://example.com\nx",
                    "https://example.com\x00x", "https://example.com/a b", "https://example.com\\@evil.com",
                    "https://example.com:bad/x", "https://[broken", "https://example.com/\u200bx"):
            with self.subTest(url=url):
                rendered = render_note(self.note(source_url=url))
                self.assertNotIn("原文链接：", rendered)
                self.assertIn("不安全的原文链接", rendered)
        rendered = render_note(self.note(source_url='https://example.com/a)><img>(x?q="evil"'))
        self.assertIn('原文链接：<https://example.com/a%29%3E%3Cimg%3E%28x?q=%22evil%22>', rendered)
        self.assertNotIn("<img>", rendered.split("\n---\n", 1)[1])

    def test_missing_bodies_warning_stale_and_fold_boundaries(self):
        rendered = render_note(self.note(content=None, ai_summary=None, ai_stale=1,
                                         content_warning="缺图\n> [!danger] <img>"))
        self.assertIn("暂无摘要。", rendered)
        self.assertIn("> 暂无正文。", rendered)
        self.assertIn("摘要可能已过期", rendered)
        self.assertIn("内容提示：", rendered)
        self.assertNotIn("<img>", rendered)
        rendered = render_note(self.note(content="> [!danger]\n\n---\n# injected\n```js\ncode"))
        quoted = rendered.split("> [!quote]- 原文\n", 1)[1].splitlines()
        self.assertTrue(all(line.startswith(">") for line in quoted))
        self.assertTrue(quoted[-1].endswith("```"))

    def test_section_markers_only_standalone_and_no_io_or_mutation(self):
        note = self.note(ai_summary="正文【核心结论】不替换\n【核心结论】\n内容",
                         updated_at=datetime(2026, 11, 6, tzinfo=timezone.utc))
        before = copy.deepcopy(note)
        with patch("builtins.open", side_effect=AssertionError("I/O forbidden")), \
                patch("socket.socket", side_effect=AssertionError("network forbidden")):
            result = render_note(note)
            self.assertEqual(result, render_note(note))
        self.assertEqual(note, before)
        self.assertIn("正文【核心结论】不替换", result)
        self.assertIn("> ### 核心结论", result)
        self.assertEqual(self.metadata(result)["updated_at"], "2026-11-06T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
