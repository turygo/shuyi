from __future__ import annotations

import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

from lxml import etree

from shuyi import application
from shuyi.contracts import Assignment, ClassificationBatch, ShuyiError
from shuyi.epub.archive import load_book
from shuyi.epub.bilingual import content_context, find_bilingual, require_bilingual
from shuyi.epub.dom import element_path, parse_xml, resolve_element
from shuyi.layout.inventory import build_inventory
from tests.fixtures import FakeClassifier, build_epub, document

SIGNATURE = "opacity: .8; color: #888; font-size: .85em; font-style: italic; margin-top: .5em"


class UnknownSourceClassifier(FakeClassifier):
    def classify(self, samples):
        batch = super().classify(samples)
        sources = {node.node_id for node in samples if node.content == "source"}
        return ClassificationBatch(
            tuple(
                Assignment(item.node_id, None) if item.node_id in sources else item
                for item in batch.observations
            ),
            batch.usage,
        )


class TestBilingual(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sequence = 0

    def book(self, body: str, **kwargs):
        self.sequence += 1
        return load_book(
            build_epub(
                self.root / f"book-{self.sequence}.epub",
                documents={"book.xhtml": document(body)},
                **kwargs,
            )
        )

    def test_exporter_block_pair_is_bounded_and_opt_in(self) -> None:
        book = self.book(
            "<p>中文正文。</p>"
            f'<p style="{SIGNATURE}">English reference.</p>'
            '<p>Ordinary English remains unchanged.</p><p style="color:#888">Gray text.</p>'
        )
        before = dict(book.entries)
        report = find_bilingual(book)
        self.assertEqual(report.issues, ())
        self.assertEqual([item.content for item in report.bindings], ["target", "source"])
        self.assertTrue(report.bindings[1].exporter)
        self.assertEqual(len({item.pair_id for item in report.bindings}), 1)
        inventory = build_inventory(book, bilingual=True)
        self.assertEqual(
            [node.content for node in inventory.nodes], ["target", "source", "unpaired", "unpaired"]
        )
        mono = build_inventory(book)
        self.assertTrue(all(node.content == "target" for node in mono.nodes))
        self.assertNotEqual(mono.digest, inventory.digest)
        self.assertNotEqual(inventory.nodes[0].group_key, inventory.nodes[1].group_key)
        self.assertEqual(book.entries, before)
        self.assertEqual(inventory, build_inventory(book, bilingual=True))

    def test_explicit_metadata_allows_different_block_tags(self) -> None:
        book = self.book(
            '<h2 data-bilingual="target" data-bilingual-pair="one">中文标题</h2>'
            '<p data-bilingual="source" data-bilingual-pair="one">English title</p>'
        )
        inventory = build_inventory(book, bilingual=True)
        self.assertEqual([node.content for node in inventory.nodes], ["target", "source"])
        self.assertEqual(inventory.nodes[0].pair_id, inventory.nodes[1].pair_id)
        self.assertEqual([node.node_id for node in inventory.nodes], ["n000001", "n000002"])

    def test_own_language_pairs_ignore_inherited_root_language(self) -> None:
        book = self.book(
            '<p lang="zh-Hans">中文正文</p><p xml:lang="EN-us">English reference</p>'
            "<p>另一段正文</p>"
            f'<p style="{SIGNATURE}">Another reference</p>'
        )
        data = book.entries["book.xhtml"].replace(b"<html ", b'<html lang="zh" ')
        book = replace(book, entries={**book.entries, "book.xhtml": data})
        report = find_bilingual(book)
        require_bilingual(report)
        self.assertEqual(len(report.bindings), 4)
        self.assertEqual(len({item.pair_id for item in report.bindings}), 2)

    def test_inline_different_tags_and_direct_text_do_not_gain_wrappers(self) -> None:
        book = self.book(
            "<p><strong>中文短语</strong>"
            f'<em style="{SIGNATURE}">English phrase</em></p>'
            "<p>中文直接文本<br/><!-- separator -->"
            f'<span style="{SIGNATURE}">Direct reference <i>emphasis</i></span></p>'
            "<p><b>前一部分</b>中文尾部"
            f'<small style="{SIGNATURE}">Tail reference</small></p>'
        )
        inventory = build_inventory(book, bilingual=True)
        self.assertEqual(
            [node.tag for node in inventory.nodes], ["p", "strong", "em", "p", "span", "p", "small"]
        )
        self.assertEqual(
            [node.content for node in inventory.nodes],
            ["mixed", "target", "source", "mixed", "source", "mixed", "source"],
        )
        self.assertEqual(len(inventory.content), 4)
        self.assertTrue(all(item.flow == "inline" for item in inventory.content))
        root = parse_xml(book.entries["book.xhtml"], "book.xhtml")
        emphasis = root.xpath('//*[local-name()="i"]')[0]
        context = content_context(
            root, element_path(root, emphasis), inventory.content, bilingual=True
        )
        self.assertEqual(context["content"], "source")
        self.assertFalse(context["content_root"])
        self.assertEqual(context["pair_id"], inventory.nodes[4].pair_id)
        self.assertEqual(len(root.xpath('//*[local-name()="span"]')), 1)

    def test_short_image_translation_signature_needs_own_english_language(self) -> None:
        book = self.book(
            "<ul><li>图片中的中文<br/>"
            '<span xml:lang="en" style="opacity:0.80; font-size:0.850em">Image reference</span>'
            '</li></ul><p lang="zh-Hans">另一段中文</p>'
            '<p lang="en" style="opacity:.8;font-size:.85em;display:block">English block</p>'
            '<p>普通中文<span style="opacity:.8;font-size:.85em">Ordinary English</span></p>'
        )
        inventory = build_inventory(book, bilingual=True)
        sources = [node for node in inventory.nodes if node.content == "source"]
        self.assertEqual([node.tag for node in sources], ["span", "p"])
        self.assertTrue(all(node.exporter for node in sources))
        self.assertEqual(inventory.nodes[0].content, "mixed")
        self.assertEqual(inventory.nodes[-1].content, "unpaired")

    def test_neutral_exporter_labels_remain_unpaired(self) -> None:
        book = self.book(
            f'<p>正文</p><p style="{SIGNATURE}">Text</p><p>12</p><p style="{SIGNATURE}">12</p>'
        )
        inventory = build_inventory(book, bilingual=True)
        self.assertEqual(
            [node.content for node in inventory.nodes], ["target", "source", "unpaired", "unpaired"]
        )

    def test_conflicts_and_missing_mates_fail_before_classifier_work(self) -> None:
        cases = (
            (
                '<p data-bilingual="target" lang="en">中文</p>'
                '<p data-bilingual="source">English</p>',
                "bilingual_language_conflict",
            ),
            (
                '<p data-bilingual="target" data-bilingual-pair="a">中文</p>'
                '<p data-bilingual="source" data-bilingual-pair="b">English</p>',
                "bilingual_pair_ambiguous",
            ),
            (
                '<p lang="zh">中文</p><p lang="en" xml:lang="zh">English</p>',
                "bilingual_language_conflict",
            ),
            (
                f'<p>中文</p><p lang="zh" style="{SIGNATURE}">English</p>',
                "bilingual_language_conflict",
            ),
        )
        for body, code in cases:
            with self.subTest(code=code, body=body):
                book = self.book(body)
                classifier = FakeClassifier()
                with self.assertRaises(ShuyiError) as raised:
                    application.style(
                        book.source_path,
                        bilingual=True,
                        classifier=classifier,
                        cache_dir=self.root / "cache",
                    )
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(classifier.calls, [])
                self.assertFalse(book.source_path.with_suffix(".shuyi.epub").exists())

    def test_unmarked_orphan_hints_never_authorize_a_paid_conversion(self) -> None:
        for body in (
            f'<p style="{SIGNATURE}">Orphan reference</p>',
            f'<p style="{SIGNATURE}">中文 with unbounded English reference</p>',
            f'<p>中文</p><hr/><p style="{SIGNATURE}">Reference</p>',
            f'<h2>中文</h2><p style="{SIGNATURE}">Reference</p>',
        ):
            book = self.book(body)
            classifier = FakeClassifier()
            with self.subTest(body=body), self.assertRaises(ShuyiError):
                application.style(
                    book.source_path,
                    bilingual=True,
                    classifier=classifier,
                    cache_dir=self.root / "cache",
                )
            self.assertEqual(classifier.calls, [])
            self.assertFalse(book.source_path.with_suffix(".shuyi.epub").exists())

    def test_protected_resources_subtrees_and_foreign_nodes_are_excluded(self) -> None:
        book = self.book(
            "<p>中文</p>"
            f'<p style="{SIGNATURE}">Reference</p>'
            '<pre><span data-bilingual="source">Orphan protected</span></pre>'
            '<p><code data-bilingual="source">Code</code></p>'
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<text data-bilingual="source">Orphan SVG</text></svg>'
            '<foreign xmlns="urn:test" data-bilingual="source">Foreign</foreign>'
        )
        inventory = build_inventory(book, bilingual=True)
        self.assertEqual(len(inventory.content), 2)
        self.assertEqual(len(inventory.nodes), 2)
        protected = replace(book, protected=frozenset({"book.xhtml"}))
        self.assertEqual(find_bilingual(protected).bindings, ())
        with self.assertRaisesRegex(ShuyiError, "bilingual_structure_missing"):
            build_inventory(protected, bilingual=True)

    def test_ordinary_mixed_language_and_gray_text_are_not_bilingual(self) -> None:
        book = self.book(
            '<p>中文 mixed English prose</p><p lang="en">Ordinary English</p>'
            '<p style="color:#888;font-style:italic">Gray English</p>'
        )
        report = find_bilingual(book)
        self.assertEqual(report.bindings, ())
        self.assertEqual(report.issues, ())
        with self.assertRaisesRegex(ShuyiError, "bilingual_structure_missing"):
            require_bilingual(report)

    def test_conflicting_target_inside_source_scope_is_rejected(self) -> None:
        book = self.book(
            '<div data-bilingual="target">中文</div>'
            '<div data-bilingual="source">English '
            '<p data-bilingual="target">冲突中文</p></div>'
        )
        report = find_bilingual(book)
        self.assertEqual(report.issues[0].code, "bilingual_nested_conflict")
        root = parse_xml(book.entries["book.xhtml"], "book.xhtml")
        source = report.bindings[-1]
        nested = resolve_element(root, source.path)[0]
        bindings = (
            *report.bindings,
            replace(source, path=element_path(root, nested), content="target"),
        )
        with self.assertRaisesRegex(ShuyiError, "bilingual_nested_conflict"):
            content_context(root, element_path(root, nested), bindings, bilingual=True)

    def test_pair_ids_do_not_destroy_semantic_groups(self) -> None:
        book = self.book(
            "<p>中文一</p>"
            f'<p style="{SIGNATURE}">Reference one</p>'
            "<p>中文二</p>"
            f'<p style="{SIGNATURE}">Reference two</p>'
        )
        inventory = build_inventory(book, bilingual=True)
        first, second = inventory.nodes[1], inventory.nodes[3]
        self.assertNotEqual(first.pair_id, second.pair_id)
        self.assertEqual(first.group_key, second.group_key)

    def test_explicit_target_with_nested_source_is_valid_mixed_scope(self) -> None:
        book = self.book(
            '<p data-bilingual="target">中文正文'
            '<span data-bilingual="source">English reference</span></p>'
        )
        inventory = build_inventory(book, bilingual=True)
        self.assertEqual([node.content for node in inventory.nodes], ["mixed", "source"])
        self.assertTrue(inventory.nodes[0].content_root)
        self.assertEqual(inventory.nodes[0].pair_id, inventory.nodes[1].pair_id)

    def test_inline_production_styling_preserves_direct_text_and_emphasis(self) -> None:
        book = self.book(
            "<p>中文正文<br/>"
            f'<span style="{SIGNATURE}">English <em>emphasis</em></span>中文结尾</p>'
        )
        result = application.style(
            book.source_path,
            bilingual=True,
            classifier=FakeClassifier(),
            cache_dir=self.root / "cache",
        )
        with zipfile.ZipFile(result["output"]) as archive:
            root = etree.fromstring(archive.read("book.xhtml"))
            paragraph = root.xpath('//*[local-name()="p"]')[0]
            source = paragraph.find("{http://www.w3.org/1999/xhtml}span")
            self.assertEqual(paragraph.get("data-sy-content"), "mixed")
            self.assertEqual(source.get("data-sy-content"), "source")
            self.assertEqual(paragraph.text, "中文正文")
            self.assertEqual(source.tail, "中文结尾")
            self.assertEqual(len(paragraph), 2)
            self.assertEqual(source[0].tag, "{http://www.w3.org/1999/xhtml}em")
        application.check(Path(result["output"]))

    def test_unknown_source_role_is_never_copied_from_target(self) -> None:
        book = self.book(f'<p>中文正文</p><p style="{SIGNATURE}">English reference</p>')
        classifier = UnknownSourceClassifier()
        result = application.style(
            book.source_path, bilingual=True, classifier=classifier, cache_dir=self.root / "cache"
        )
        self.assertTrue(classifier.calls)
        with zipfile.ZipFile(result["output"]) as archive:
            root = etree.fromstring(archive.read("book.xhtml"))
            paragraphs = root.xpath('//*[local-name()="p"]')
            self.assertEqual(paragraphs[0].get("data-sy-content"), "target")
            self.assertIsNone(paragraphs[1].get("data-sy-node"))
            self.assertEqual(paragraphs[1].get("style"), SIGNATURE)
