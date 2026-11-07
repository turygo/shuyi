from __future__ import annotations

import unittest
from pathlib import Path

from shuyi.contracts import Book, ShuyiError
from shuyi.epub.dom import element_path, parse_xml
from shuyi.epub.styles import (
    demote_inline,
    parse_rules,
    specificity_guard,
    style_evidence,
    validate_value,
)


def css_book(css):
    data = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        b'<link rel="stylesheet" href="style.css"/></head><body><p>First</p>'
        b'<p style="color:blue">Second</p></body></html>'
    )
    book = Book(
        Path("fixture.epub"),
        "source",
        {"chapter.xhtml": data, "style.css": css.encode()},
        {},
        "content.opf",
        ("chapter.xhtml",),
        frozenset(),
        "2.0",
    )
    return book, parse_xml(data, "chapter.xhtml")


class StyleTests(unittest.TestCase):
    def test_nth_child_and_inline_evidence_differ(self):
        book, root = css_book("@page {margin:1em} p:nth-child(2){font-weight:bold}")
        evidence = style_evidence(book, "chapter.xhtml", root)
        first, second = root.findall(".//{http://www.w3.org/1999/xhtml}p")
        self.assertNotEqual(
            evidence[element_path(root, first)], evidence[element_path(root, second)]
        )
        self.assertIn("font-weight", evidence[element_path(root, second)])
        self.assertNotIn("font-weight", evidence[element_path(root, first)])

    def test_guard_exceeds_author_ids(self):
        book, root = css_book("#one #two p {color:red !important}")
        self.assertEqual(specificity_guard(book, "chapter.xhtml", root), 3)

    def test_source_page_and_font_face_allowed_but_import_fails(self):
        self.assertEqual(
            parse_rules("@page{margin:0}@font-face{font-family:x;src:url(font.otf)}"), ()
        )
        with self.assertRaises(ShuyiError):
            parse_rules('@import "remote.css";')

    def test_custom_unsafe_css_rejected(self):
        cases = [
            "@font-face{font-family:x}",
            '@import "x";',
            "p:hover{color:red}",
            'p::before{content:"x"}',
            "p{position:absolute}",
            "p{color:var(--x)}",
            "p{background-color:url(x)}",
            "@supports(display:grid){p{color:red}}",
            "p{--color:red}",
        ]
        for css in cases:
            with self.subTest(css=css), self.assertRaises(ShuyiError):
                parse_rules(css, custom=True)

    def test_dark_query_and_static_selector_supported(self):
        rules = parse_rules(
            "@media (prefers-color-scheme: dark){p:nth-child(2){color:#fff}}", custom=True
        )
        self.assertEqual(rules[0].condition, "(prefers-color-scheme: dark)")

    def test_inline_full_coverage_only(self):
        self.assertEqual(
            demote_inline("margin:1em !important;color:red", {"margin"}), "margin:1em;color:red;"
        )
        with self.assertRaises(ShuyiError):
            demote_inline("margin:1em !important", {"margin-top"})
        with self.assertRaises(ShuyiError):
            demote_inline("font:italic 1em serif !important", {"font-size"})
        self.assertEqual(
            demote_inline("color:red!important;font-size:2em!important", {"color"}),
            "color:red;font-size:2em !important;",
        )

    def test_font_variant_and_page_break_aliases_are_fully_covered(self):
        self.assertEqual(
            demote_inline("font-variant-ligatures:none!important", {"font-variant"}),
            "font-variant-ligatures:none;",
        )
        self.assertEqual(
            demote_inline("page-break-after:always!important", {"break-after"}),
            "page-break-after:always;",
        )

    def test_invalid_theme_values_cannot_authorize_inline_demotion(self):
        for css in (
            "p{color:}",
            "p{color:not-a-color}",
            "p{font-size:potato}",
            "p{text-decoration:solid dotted}",
            "p{text-decoration:red blue}",
        ):
            with self.subTest(css=css), self.assertRaises(ShuyiError):
                parse_rules(css, custom=True)

    def test_source_namespace_is_not_silently_discarded(self):
        with self.assertRaisesRegex(ShuyiError, "css_namespace_unsupported"):
            parse_rules('@namespace "urn:other"; p:nth-child(2){font-weight:bold}')

    def test_note_only_dimensions_bounded(self):
        with self.assertRaises(ShuyiError):
            validate_value("width", "1.8em")
        validate_value("width", "1.8em", note=True)
        with self.assertRaises(ShuyiError):
            validate_value("width", "100em", note=True)

    def test_generated_assets_cannot_hide_font_face_or_page_rules(self):
        for css in ["@font-face{font-family:x;src:url(x)}", "@page{margin:0}"]:
            with self.subTest(css=css), self.assertRaises(ShuyiError):
                parse_rules(css, generated=True)

    def test_specificity_guard_cap(self):
        book, root = css_book("".join(f"#id{i}" for i in range(128)) + "{color:red}")
        with self.assertRaises(ShuyiError):
            specificity_guard(book, "chapter.xhtml", root)
