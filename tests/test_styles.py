from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from shuyi import application
from shuyi.contracts import Book, ShuyiError
from shuyi.epub.archive import load_book
from shuyi.epub.dom import element_path, parse_xml
from shuyi.epub.styles import (
    compile_selector,
    demote_inline,
    parse_rules,
    specificity_guard,
    style_evidence,
    validate_value,
)
from shuyi.layout.inventory import build_inventory
from tests.fixtures import FakeClassifier, build_epub, document


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

    def test_source_namespace_drives_consumer_matching(self):
        cases = [
            ('@namespace "urn:other"; p{font-weight:bold}', False),
            ('@namespace "http://www.w3.org/1999/xhtml"; p{font-weight:bold}', True),
            ('@namespace h url("http://www.w3.org/1999/xhtml"); h|p{font-weight:bold}', True),
            ('@namespace h "urn:other"; h|p{font-weight:bold}', False),
            ('@namespace e "http://www.idpf.org/2007/ops"; [e|type]{font-weight:bold}', True),
        ]
        for css, expected in cases:
            with self.subTest(css=css), tempfile.TemporaryDirectory() as directory:
                source = build_epub(
                    Path(directory) / "source.epub",
                    documents={"book.xhtml": document('<p epub:type="bodymatter">Body.</p>')},
                    stylesheet=css,
                )
                inventory = build_inventory(load_book(source))
                self.assertEqual("font-weight" in inventory.nodes[0].source_css, expected)
        for custom in (True, False):
            with self.subTest(custom=custom), self.assertRaises(ShuyiError):
                parse_rules(
                    '@namespace "urn:other"; p{color:red}', custom=custom, generated=not custom
                )

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

    def test_source_pseudo_and_state_evidence_retains_guard(self):
        book, root = css_book(
            "#one #two p:hover::first-letter {font-weight:bold}"
            "p:focus {color:red} p::first-line {font-style:italic}"
        )
        evidence = style_evidence(book, "chapter.xhtml", root)
        first = root.find(".//{http://www.w3.org/1999/xhtml}p")
        own = json.loads(evidence[element_path(root, first)])[-1]["css"]
        self.assertEqual({record[0] for record in own}, {"p:focus", "p::first-line"})
        self.assertEqual(specificity_guard(book, "chapter.xhtml", root), 3)
        book, root = css_book("p:hover::first-letter {font-weight:bold}")
        self.assertIn("p:hover::first-letter", style_evidence(book, "chapter.xhtml", root)[(1, 0)])

    def test_source_link_states_only_match_static_link_subject(self):
        book, root = css_book("a:visited {color:red} a:link {font-weight:bold}")
        body = root.find("{http://www.w3.org/1999/xhtml}body")
        link = parse_xml(b'<a xmlns="http://www.w3.org/1999/xhtml" href="#x">Link</a>', "test")
        anchor = parse_xml(b'<a xmlns="http://www.w3.org/1999/xhtml">Anchor</a>', "test")
        body.extend([link, anchor])
        evidence = style_evidence(book, "chapter.xhtml", root)
        self.assertIn("a:visited", evidence[element_path(root, link)])
        self.assertNotIn("a:visited", evidence[element_path(root, anchor)])

    def test_nested_static_media_and_link_media_are_labeled(self):
        book, root = css_book(
            "@media amzn-kf8, amzn-mobi {@media screen and (min-width: 30em) {p{font-weight:bold}}}"
        )
        root.find(".//{http://www.w3.org/1999/xhtml}link").set(
            "media", "only screen and (max-device-width: 1200px), amzn-et"
        )
        evidence = style_evidence(book, "chapter.xhtml", root)[(1, 0)]
        for label in ("amzn-kf8", "amzn-mobi", "min-width", "max-device-width", "amzn-et"):
            self.assertIn(label, evidence)
        rules = parse_rules("@media not print and (orientation:portrait){p{color:red}}")
        self.assertEqual(rules[0].condition, "not print and (orientation:portrait)")

    def test_source_unsupported_grammar_fails_instead_of_dropping(self):
        cases = [
            "p:not(:hover){color:red}",
            "p:has(a){color:red}",
            "p::selection{color:red}",
            "@media (unknown:1){p{color:red}}",
            "@media screen and {p{color:red}}",
            "@media not {p{color:red}}",
            "@media (width:calc(10px)){p{color:red}}",
            "@layer x {p{color:red}}",
            "p{& span{color:red}}",
            "p{all:unset}",
            '@import "x.css";',
            'p{color:red}@namespace "urn:late";',
        ]
        for css in cases:
            with self.subTest(css=css), self.assertRaises(ShuyiError):
                parse_rules(css)

    def test_custom_and_generated_source_extensions_stay_restricted(self):
        cases = [
            "p::first-letter{color:red}",
            "p:first-line{color:red}",
            "a:visited{color:red}",
            "p:focus{color:red}",
            "@media amzn-kf8{p{color:red}}",
            "@media screen and (min-width:1px){p{color:red}}",
        ]
        for css in cases:
            for mode in ("custom", "generated"):
                with self.subTest(css=css, mode=mode), self.assertRaises(ShuyiError):
                    parse_rules(css, **{mode: True})
        with self.assertRaises(ShuyiError):
            compile_selector("p:hover")

    def test_compatibility_styles_preserve_css_bytes_through_application(self):
        css = (
            b'@namespace h "http://www.w3.org/1999/xhtml";\n'
            b"@media amzn-kf8, screen and (min-width: 1px) {\n"
            b" h|p:hover::first-letter { color:red; }\n"
            b" @media (orientation:portrait) {h|p{font-weight:bold}}\n}\n"
            b"@font-face{font-family:Example;src:url(example.ttf)}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = build_epub(
                home / "source.epub",
                stylesheet=css,
                extra_entries={"example.ttf": b"fixture-font"},
            )
            result = application.style(
                source, classifier=FakeClassifier(), cache_dir=home / "cache"
            )
            with zipfile.ZipFile(result["output"]) as archive:
                self.assertEqual(archive.read("styles.css"), css)
                self.assertEqual(archive.read("example.ttf"), b"fixture-font")
            application.check(Path(result["output"]))

    def test_reference_opacity_permission_is_exact_and_not_custom(self):
        validate_value("opacity", "1", reference=True)
        for kwargs in ({}, {"note": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ShuyiError):
                validate_value("opacity", "1", **kwargs)
        for value in ("0.8", "0", "1.0", "inherit", "var(--opacity)", "1!important"):
            with self.subTest(value=value), self.assertRaises(ShuyiError):
                validate_value("opacity", value, reference=True)
        with self.assertRaises(ShuyiError):
            parse_rules("p{opacity:1}", custom=True)


class SourceConditionalTests(unittest.TestCase):
    def test_supports_and_image_only_animation_survive_styling(self):
        css = (
            "@media (hover:hover) and (pointer:fine){"
            "@supports (display:flex){p{color:blue}}"
            "a:hover img,a:focus img,a:active img{animation:thumb .2s}}"
            "@supports not (break-after:always){p{page-break-after:always}}"
            "@supports ((display:flex) or (display:grid)){img{width:2em}}"
            "@keyframes thumb{from{width:1em}to{width:2em}}"
            "@-webkit-keyframes thumb{0%{width:1em}100%{width:2em}}"
        )
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = build_epub(
                home / "source.epub",
                documents={
                    "book.xhtml": document(
                        '<p>Prose.</p><a href="#x" id="x"><img src="x.png"/></a>'
                    )
                },
                stylesheet=css,
                extra_entries={"x.png": b"fixture-image"},
            )
            inventory = build_inventory(load_book(source))
            self.assertIn("@supports (display:flex)", inventory.nodes[0].source_css)
            self.assertIn("hover:hover", inventory.nodes[0].source_css)
            result = application.style(
                source, classifier=FakeClassifier(), cache_dir=home / "cache"
            )
            with zipfile.ZipFile(result["output"]) as archive:
                self.assertEqual(archive.read("styles.css"), css.encode())
            application.check(Path(result["output"]))

    def test_text_related_dynamic_cascades_fail_even_when_conditional(self):
        for selector in ("p", "body", "html", "p:hover"):
            for property_name in ("animation", "-webkit-animation", "transition"):
                css = f"@media (hover:hover){{{selector}{{{property_name}:thumb .2s}}}}"
                book, root = css_book(css)
                with (
                    self.subTest(selector=selector, property=property_name),
                    self.assertRaisesRegex(ShuyiError, "css_cascade_unsupported"),
                ):
                    style_evidence(book, "chapter.xhtml", root)

    def test_prefixed_subject_pseudos_ignore_an_unrelated_default_namespace(self):
        namespaces = '@namespace "urn:other"; @namespace h "http://www.w3.org/1999/xhtml";'
        for suffix in (":hover", "::first-letter", ":hover::first-letter"):
            book, root = css_book(namespaces + f"h|p{suffix}{{font-weight:bold}}")
            first = root.find(".//{http://www.w3.org/1999/xhtml}p")
            evidence = style_evidence(book, "chapter.xhtml", root)[element_path(root, first)]
            with self.subTest(suffix=suffix):
                self.assertIn("font-weight", evidence)
        book, root = css_book(namespaces + "h|p:hover{animation:thumb .2s}")
        with self.assertRaisesRegex(ShuyiError, "css_cascade_unsupported"):
            style_evidence(book, "chapter.xhtml", root)

    def test_inline_text_animation_stops_before_analysis_or_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = build_epub(
                home / "inline.epub",
                documents={"book.xhtml": document('<p style="animation:thumb .2s">Prose.</p>')},
            )
            classifier = FakeClassifier()
            with self.assertRaisesRegex(ShuyiError, "css_cascade_unsupported"):
                application.style(source, classifier=classifier, cache_dir=home / "cache")
            self.assertEqual(classifier.calls, [])
            self.assertFalse(source.with_suffix(".shuyi.epub").exists())

    def test_conditional_extensions_do_not_relax_custom_or_malformed_source(self):
        for css in (
            "@supports (display:flex){p{color:red}}",
            "@keyframes thumb{from{width:1em}to{width:2em}}",
            "img{animation:thumb .2s}",
        ):
            for mode in ("custom", "generated"):
                with self.subTest(css=css, mode=mode), self.assertRaises(ShuyiError):
                    parse_rules(css, **{mode: True})
        for css in (
            "@supports (display:url(x)){p{color:red}}",
            "@supports not {p{color:red}}",
            "@supports (display:flex) and {p{color:red}}",
            "@supports selector(p){p{color:red}}",
            "@keyframes thumb{101%{width:1em}}",
            "@keyframes thumb{from{@import 'x';}}",
        ):
            with self.subTest(css=css), self.assertRaises(ShuyiError):
                parse_rules(css)
