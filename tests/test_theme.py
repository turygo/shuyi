from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from shuyi.contracts import Assignment, Book, Inventory, LayoutNode, LayoutProfile, ShuyiError
from shuyi.epub.dom import element_path, node_digest, parse_xml
from shuyi.epub.styles import validate_generated_css
from shuyi.theme.service import plan_theme

XHTML = "http://www.w3.org/1999/xhtml"


def theme_book(body, css="p{color:blue}", version="2.0"):
    document = (
        f'<html xmlns="{XHTML}" xmlns:epub="http://www.idpf.org/2007/ops">'
        '<head><link rel="stylesheet" href="source.css"/></head>'
        f"<body>{body}</body></html>"
    ).encode()
    opf = (
        f'<package xmlns="http://www.idpf.org/2007/opf" version="{version}">'
        '<metadata/><manifest><item id="chapter" href="chapter.xhtml" '
        'media-type="application/xhtml+xml"/><item id="css" href="source.css" '
        'media-type="text/css"/></manifest><spine><itemref idref="chapter"/></spine></package>'
    ).encode()
    return Book(
        Path("fixture.epub"),
        "source",
        {"chapter.xhtml": document, "source.css": css.encode(), "content.opf": opf},
        {},
        "content.opf",
        ("chapter.xhtml",),
        frozenset(),
        version,
    )


def profile_for(book, roles):
    root = parse_xml(book.entries["chapter.xhtml"], "chapter.xhtml")
    paragraphs = root.findall(f".//{{{XHTML}}}p")
    nodes, assignments = [], {}
    for index, (node, role) in enumerate(zip(paragraphs, roles, strict=True), 1):
        node_id = f"n{index:06d}"
        nodes.append(
            LayoutNode(
                node_id,
                "chapter.xhtml",
                element_path(root, node),
                node_digest(node),
                "p",
                "".join(node.itertext()),
                "",
                "",
                "",
                "",
                "group",
            )
        )
        assignments[node_id] = Assignment(node_id, role, 1 if role == "heading" else None)
    return Inventory("source", "inventory", tuple(nodes)), LayoutProfile(
        "source", "inventory", assignments, "fixture"
    )


class ThemeTests(unittest.TestCase):
    def test_builtin_roles_and_unknown_preserve_source(self):
        book = theme_book("<p>Title</p><p>Body <em>emphasis</em></p><p>Unknown</p>")
        inventory, profile = profile_for(book, ["heading", "body", None])
        plan = plan_theme(book, inventory, profile)
        root = parse_xml(plan.replacements["chapter.xhtml"], "chapter.xhtml")
        paragraphs = root.findall(f".//{{{XHTML}}}p")
        self.assertEqual(paragraphs[0].get("data-sy-level"), "1")
        self.assertEqual(paragraphs[1].get("data-sy-role"), "body")
        self.assertEqual(paragraphs[2].attrib, {})
        self.assertEqual(paragraphs[1][0].text, "emphasis")
        self.assertNotIn("source.css", plan.replacements)
        output = replace(book, entries={**book.entries, **plan.replacements})
        validate_generated_css(book, plan, output)

    def test_broad_custom_selector_only_targets_admitted_node(self):
        book = theme_book('<p style="color:red!important">Body</p><p>Unknown</p>')
        inventory, profile = profile_for(book, ["body", None])
        plan = plan_theme(book, inventory, profile, b"* {color:green}")
        css = plan.replacements[plan.asset_path].decode()
        self.assertIn('[data-sy-node="n000001"]:not(#shuyi-never)', css)
        self.assertNotIn("n000002", css)
        self.assertNotIn("font-family", css)
        root = parse_xml(plan.replacements["chapter.xhtml"], "chapter.xhtml")
        self.assertEqual(root.find(f".//{{{XHTML}}}p").get("style"), "color:red;")

    def test_partial_inline_shorthand_fails(self):
        book = theme_book('<p style="margin:0!important">Body</p>')
        inventory, profile = profile_for(book, ["body"])
        with self.assertRaises(ShuyiError):
            plan_theme(book, inventory, profile, b"p{margin-top:1em}")

    def test_confirmed_nested_note_and_arbitrary_number(self):
        book = theme_book(
            '<p>Body <a id="r" epub:type="noteref" href="#n"><sup>1</sup></a> tail '
            '<a href="#other">2</a></p><aside id="n" epub:type="footnote">'
            '<a href="#r">↩</a> Note</aside><div id="other">Other</div>'
        )
        inventory, profile = profile_for(book, [None])
        plan = plan_theme(book, inventory, profile)
        root = parse_xml(plan.replacements["chapter.xhtml"], "chapter.xhtml")
        anchor = root.xpath('//*[@id="r"]')[0]
        self.assertEqual("".join(anchor.itertext()), "注")
        self.assertEqual(anchor[0].tag, f"{{{XHTML}}}sup")
        self.assertEqual(anchor.tail, " tail ")
        self.assertEqual(anchor.get("href"), "#n")
        self.assertEqual(anchor.get("data-sy-note-id"), "note000001")
        self.assertIsNone(anchor.get("data-sy-node"))
        self.assertEqual(root.xpath('//*[local-name()="a"][@href="#other"]')[0].text, "2")
        self.assertEqual(plan.note_count, 2)
        validate_generated_css(
            book, plan, replace(book, entries={**book.entries, **plan.replacements})
        )

    def test_custom_css_keeps_note_label_policy(self):
        book = theme_book(
            '<p><a epub:type="noteref" href="#n">1</a></p>'
            '<aside id="n" epub:type="footnote">Note</aside>'
        )
        inventory, profile = profile_for(book, [None])
        plan = plan_theme(book, inventory, profile, b"p{color:red}")
        root = parse_xml(plan.replacements["chapter.xhtml"], "chapter.xhtml")
        self.assertEqual(root.find(f".//{{{XHTML}}}a").text, "注")
        self.assertNotIn(b"background-color", plan.replacements[plan.asset_path])

    def test_digest_protected_and_repeated_styling_rejected(self):
        book = theme_book("<p>Body</p>")
        inventory, profile = profile_for(book, ["body"])
        bad = replace(inventory, nodes=(replace(inventory.nodes[0], source_sha256="wrong"),))
        with self.assertRaises(ShuyiError):
            plan_theme(book, bad, profile)
        with self.assertRaises(ShuyiError):
            plan_theme(replace(book, protected=frozenset({"chapter.xhtml"})), inventory, profile)
        plan = plan_theme(book, inventory, profile)
        with self.assertRaises(ShuyiError):
            plan_theme(
                replace(book, entries={**book.entries, **plan.replacements}), inventory, profile
            )

    def test_generated_css_tampering_rejected(self):
        book = theme_book("<p>Body</p>")
        inventory, profile = profile_for(book, ["body"])
        plan = plan_theme(book, inventory, profile)
        entries = {**book.entries, **plan.replacements, plan.asset_path: b"p{color:red!important}"}
        with self.assertRaises(ShuyiError):
            validate_generated_css(book, plan, replace(book, entries=entries))

    def test_epub3_modified_update_is_reversible(self):
        book = theme_book("<p>Body</p>", version="3.0")
        old = b'<metadata><meta property="dcterms:modified">2000-01-01T00:00:00Z</meta></metadata>'
        book = replace(
            book,
            entries={
                **book.entries,
                "content.opf": book.entries["content.opf"].replace(b"<metadata/>", old),
            },
        )
        inventory, profile = profile_for(book, ["body"])
        plan = plan_theme(book, inventory, profile)
        edits = [edit for edit in plan.texts if edit.resource == "content.opf"]
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0].before, "2000-01-01T00:00:00Z")
        self.assertRegex(edits[0].after, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_source_guard_and_reserved_marker_collisions(self):
        for body in ['<p id="shuyi-never">Body</p>', '<p data-sy-note-id="note000001">Body</p>']:
            with self.subTest(body=body):
                book = theme_book(body)
                inventory, profile = profile_for(book, ["body"])
                with self.assertRaises(ShuyiError):
                    plan_theme(book, inventory, profile)

    def test_custom_dark_and_important_order_preserved(self):
        book = theme_book("<p>Body</p>")
        inventory, profile = profile_for(book, ["body"])
        custom = (
            b"p{color:red!important}@media (prefers-color-scheme: dark){p{color:blue}}"
            b"p{color:green}"
        )
        plan = plan_theme(book, inventory, profile, custom)
        css = plan.replacements[plan.asset_path].decode()
        self.assertGreater(css.index("color:red"), css.index("color:blue"))
        self.assertGreater(css.index("color:red"), css.index("color:green"))

    def test_note_comment_visible_tail_is_explicit_error(self):
        book = theme_book(
            '<p><a epub:type="noteref" href="#n">1<!--keep-->extra</a></p>'
            '<aside id="n" epub:type="footnote">Note</aside>'
        )
        inventory, profile = profile_for(book, [None])
        with self.assertRaises(ShuyiError) as caught:
            plan_theme(book, inventory, profile)
        self.assertEqual(caught.exception.code, "theme_note_label_unsupported")

    def test_untyped_symbol_note_gets_compatible_role(self):
        book = theme_book(
            '<p><a id="r" href="#n">*</a></p><aside id="n"><a href="#r">↩</a> Note</aside>'
        )
        inventory, profile = profile_for(book, [None])
        plan = plan_theme(book, inventory, profile)
        root = parse_xml(plan.replacements["chapter.xhtml"], "chapter.xhtml")
        anchors = root.findall(f".//{{{XHTML}}}a")
        self.assertEqual(
            [anchor.get("role") for anchor in anchors], ["doc-noteref", "doc-backlink"]
        )
        self.assertEqual(["".join(anchor.itertext()) for anchor in anchors], ["注", "注"])
