from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from shuyi.application import style
from shuyi.contracts import Assignment, ClassificationBatch, LayoutProfile, ShuyiError
from shuyi.epub.archive import load_book, write_book
from shuyi.epub.dom import parse_xml, resolve_element, serialize_xml
from shuyi.epub.styles import matching_rules, parse_rules
from shuyi.layout.inventory import build_inventory
from shuyi.theme.service import plan_theme
from shuyi.verify.preservation import check_book, verify_output
from tests.fixtures import build_epub, document

EXPORTER = "opacity:.8;color:#888;font-size:.85em;font-style:italic;margin-top:.5em"


class BilingualClassifier:
    provenance = "offline-bilingual-fixture"

    def classify(self, samples):
        observations = []
        for node in samples:
            role, level = "body", None
            if "Unknown" in node.text:
                role = None
            elif node.tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                role, level = "heading", int(node.tag[1])
            elif node.tag == "blockquote":
                role = "quote"
            elif node.tag == "li":
                role = "list-text"
            observations.append(Assignment(node.node_id, role, level))
        return ClassificationBatch(tuple(observations), {})


def declarations_for(book, node_id, *, dark=False):
    root = parse_xml(book.entries["book.xhtml"], "book.xhtml")
    node = root.xpath("//*[@id=$value]", value=node_id)[0]
    from shuyi.epub.dom import element_path

    rules = parse_rules(book.entries["shuyi/reading.css"].decode(), generated=True)
    matched = matching_rules(root, rules)[element_path(root, node)]
    values = {}
    for rule in matched:
        if rule.condition and not dark:
            continue
        values.update((name, value) for name, value, _ in rule.declarations)
    return node, values


class BilingualThemeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def styled(self, body, *, custom=None, version="3.0"):
        source = build_epub(
            self.directory / "source.epub",
            version=version,
            documents={"book.xhtml": document(body)},
            extra_entries={"image.png": b"fixture-image", "font.ttf": b"fixture-font"},
        )
        custom_path = None
        if custom is not None:
            custom_path = self.directory / "custom.css"
            custom_path.write_text(custom)
        output = self.directory / "output.epub"
        report = style(
            source,
            output,
            bilingual=True,
            classifier=BilingualClassifier(),
            cache_dir=self.directory / "cache",
            css_path=custom_path,
        )
        return load_book(source), load_book(output), report

    def test_exporter_body_and_heading_reference_appearance_and_preservation(self):
        body = (
            '<h1 id="title">中文标题</h1>'
            f'<h1 id="reference-title" style="{EXPORTER}">English title</h1>'
            '<p id="primary">中文正文<a href="#primary">链接</a>。</p>'
            f'<p id="reference" style="{EXPORTER}">English body '
            '<em id="emphasis">emphasis</em> <strong>strong</strong>.'
            '<a id="r" href="#n" epub:type="noteref"><sup>1</sup></a></p>'
            '<p id="unpaired" style="color:purple">单独附加内容。</p>'
            "<p>未知译文。</p>"
            f'<p id="unknown" style="{EXPORTER}">Unknown reference.</p>'
            '<aside id="n" epub:type="footnote"><a href="#r">↩</a> Note prose.</aside>'
        )
        source, output, report = self.styled(body)
        primary, primary_values = declarations_for(output, "primary")
        reference, values = declarations_for(output, "reference")
        _, dark = declarations_for(output, "reference", dark=True)
        _, heading = declarations_for(output, "reference-title")
        self.assertEqual(primary.get("data-sy-content"), "target")
        self.assertEqual(primary_values["font-size"], "1.15em")
        self.assertEqual(reference.get("data-sy-content"), "source")
        self.assertAlmostEqual(float(values["font-size"][:-2]), 1.15 * 0.82)
        self.assertEqual(values["font-family"], "Arial,Helvetica,sans-serif")
        self.assertEqual(values["font-style"], "normal")
        self.assertEqual(values["font-weight"], "normal")
        self.assertEqual(values["opacity"], "1")
        self.assertEqual(values["text-indent"], "0")
        self.assertEqual(values["color"], "#6b7280")
        self.assertEqual(dark["color"], "#929dac")
        self.assertEqual(dark["border-left-color"], "#48515e")
        self.assertEqual(values["border-left"], "1px solid #d8dfe7")
        self.assertEqual(heading["text-align"], "center")
        self.assertEqual(heading["color"], values["color"])
        self.assertNotIn("border-left", heading)
        emphasis, emphasis_values = declarations_for(output, "emphasis")
        self.assertEqual(emphasis.tag.rsplit("}", 1)[-1], "em")
        self.assertNotIn("font-style", emphasis_values)
        for identity in ("unknown", "unpaired"):
            node, node_values = declarations_for(output, identity)
            self.assertIsNone(node.get("data-sy-node"))
            self.assertFalse(node_values)
        for resource in ("styles.css", "image.png", "font.ttf"):
            self.assertEqual(output.entries[resource], source.entries[resource])
        self.assertEqual(primary[0].get("href"), "#primary")
        root = parse_xml(output.entries["book.xhtml"], "book.xhtml")
        anchor = root.xpath('//*[@id="r"]')[0]
        self.assertEqual(anchor.get("href"), "#n")
        self.assertEqual("".join(anchor.itertext()), "注")
        self.assertEqual(root.xpath('//*[@id="n"]')[0][0].get("href"), "#r")
        self.assertEqual(report["note_markers"], 2)
        self.assertTrue(check_book(output)["generated_theme"])
        self.assertGreater(report["unpaired_nodes"], 0)
        self.assertGreater(report["content_counts"]["source"], 0)

    def test_inline_mixed_native_emphasis_and_nested_scope_do_not_repeat_scaling(self):
        body = (
            '<p id="mixed">中文段落'
            f'<em id="inline" style="{EXPORTER}">English '
            "<strong>emphasis</strong></em>。</p>"
            '<blockquote lang="zh"><p>中文引文。</p></blockquote>'
            f'<blockquote id="block-source" lang="en"><p id="nested" style="{EXPORTER}">'
            "English quote <em>native emphasis</em>.</p>"
            '<p id="author-opacity" style="opacity:.4">Author opacity.</p></blockquote>'
            '<ul><li lang="zh">中文列表。</li>'
            '<li id="list-source" lang="en">English list.</li></ul>'
        )
        _, output, _ = self.styled(body)
        mixed, parent = declarations_for(output, "mixed")
        inline, values = declarations_for(output, "inline")
        self.assertEqual(mixed.get("data-sy-content"), "mixed")
        self.assertEqual(inline.get("data-sy-content"), "source")
        self.assertEqual(values["font-size"], "0.82em")
        self.assertAlmostEqual(float(parent["font-size"][:-2]) * 0.82, 0.943)
        self.assertNotIn("font-style", values)
        self.assertFalse(any(name.startswith(("margin", "padding", "border")) for name in values))
        _, outer = declarations_for(output, "block-source")
        _, nested = declarations_for(output, "nested")
        self.assertEqual(outer["font-size"], "0.943em")
        self.assertEqual(nested["font-size"], "inherit")
        self.assertNotIn("margin", nested)
        self.assertEqual(nested["opacity"], "1")
        self.assertEqual(nested["font-style"], "normal")
        author, author_values = declarations_for(output, "author-opacity")
        self.assertEqual(author.get("style"), "opacity:.4")
        self.assertNotIn("opacity", author_values)
        _, structural = declarations_for(output, "list-source")
        self.assertEqual(structural["font-size"], "0.82em")
        self.assertNotIn("border-left", structural)
        self.assertNotIn("padding-left", structural)

    def test_custom_css_replaces_appearance_without_expanding_opacity_permission(self):
        _, output, _ = self.styled(
            f'<p>中文正文。</p><p id="reference" style="{EXPORTER}">English reference.</p>',
            custom='[data-sy-content="source"]{color:green}',
            version="2.0",
        )
        node, values = declarations_for(output, "reference")
        self.assertEqual(node.get("data-sy-content"), "source")
        self.assertEqual(values, {"color": "green"})
        with self.assertRaises(ShuyiError):
            parse_rules('[data-sy-content="source"]{opacity:1}', custom=True)

    def test_block_source_size_accounts_for_primary_parent(self):
        _, output, _ = self.styled(
            '<div id="primary-container">中文引言'
            '<p lang="zh">中文段落。</p>'
            '<p id="nested-reference" lang="en">English paragraph.</p></div>'
        )
        _, parent = declarations_for(output, "primary-container")
        _, values = declarations_for(output, "nested-reference")
        self.assertEqual(parent["font-size"], "1.15em")
        self.assertAlmostEqual(float(values["font-size"][:-2]) * 1.15, 0.943, places=6)

    def test_short_exporter_inline_source_restores_opacity_once(self):
        _, output, _ = self.styled(
            '<ul><li id="mixed">中文图片译文<br/>'
            '<span id="reference" xml:lang="en" '
            'style="opacity:0.8;font-size:0.85em;">English image translation.</span>'
            "</li></ul>"
        )
        _, values = declarations_for(output, "reference")
        self.assertEqual(values["opacity"], "1")
        self.assertEqual(values["font-size"], "0.82em")
        self.assertNotIn("border-left", values)

    def test_bounded_table_text_and_inline_targets_keep_their_geometry(self):
        _, output, _ = self.styled(
            '<p><strong id="target" data-bilingual="target">中文短语</strong>'
            '<span data-bilingual="source">English phrase.</span></p>'
            '<table><tr><td id="cell">中文单元格<sup>1</sup>'
            f'<div id="reference" style="{EXPORTER}">English cell.</div>'
            "</td></tr></table>"
        )
        target, target_values = declarations_for(output, "target")
        self.assertEqual(target.tag.rsplit("}", 1)[-1], "strong")
        self.assertFalse(
            {"font-size", "text-indent", "margin", "padding-left"} & target_values.keys()
        )
        cell, _ = declarations_for(output, "cell")
        self.assertEqual(cell.get("data-sy-content"), "mixed")
        _, values = declarations_for(output, "reference")
        self.assertEqual(values["font-size"], "0.82em")
        self.assertFalse(any(name.startswith(("margin", "padding", "border")) for name in values))

    def test_identical_element_paths_in_different_documents_do_not_share_identity(self):
        source = build_epub(
            self.directory / "multiple.epub",
            documents={
                "first.xhtml": document(f'<p>中文正文。</p><p style="{EXPORTER}">Reference.</p>'),
                "second.xhtml": document(
                    "<p>Unpaired entry.</p><p>另一段正文。</p>"
                    f'<p style="{EXPORTER}">Another reference.</p>'
                ),
            },
        )
        report = style(
            source,
            bilingual=True,
            classifier=BilingualClassifier(),
            cache_dir=self.directory / "cache",
        )
        output = load_book(Path(report["output"]))
        first = parse_xml(output.entries["first.xhtml"], "first.xhtml")
        second = parse_xml(output.entries["second.xhtml"], "second.xhtml")
        path = (1, 1)
        self.assertEqual(resolve_element(first, path).get("data-sy-content"), "source")
        self.assertEqual(resolve_element(second, path).get("data-sy-content"), "target")

    def test_exporter_important_values_are_only_priority_demoted(self):
        import tinycss2

        important = EXPORTER.replace(";", "!important;") + "!important"
        source, output, _ = self.styled(
            f'<p>中文正文。</p><p id="reference" style="{important}">English paragraph.</p>'
        )
        before = parse_xml(source.entries["book.xhtml"], "book.xhtml")
        original = before.xpath('//*[@id="reference"]')[0]
        node, values = declarations_for(output, "reference")
        old = tinycss2.parse_declaration_list(original.get("style"))
        new = tinycss2.parse_declaration_list(node.get("style"))
        self.assertEqual(
            [(item.lower_name, tinycss2.serialize(item.value).strip()) for item in old],
            [(item.lower_name, tinycss2.serialize(item.value).strip()) for item in new],
        )
        self.assertTrue(all(item.important for item in old))
        self.assertTrue(all(not item.important for item in new))
        self.assertEqual(values["opacity"], "1")

    def test_nested_exporter_inline_text_restores_reference_style_without_rescaling(self):
        _, output, _ = self.styled(
            '<div data-bilingual="target"><p>中文正文。</p></div>'
            '<div data-bilingual="source"><p>English reference '
            f'<span id="nested-reference" style="{EXPORTER}">nested '
            '<em id="native-emphasis">emphasis</em></span></p></div>'
        )
        node, values = declarations_for(output, "nested-reference")
        self.assertEqual(node.get("data-sy-content"), "source")
        self.assertEqual(values["font-size"], "inherit")
        self.assertEqual(values["opacity"], "1")
        self.assertEqual(values["font-style"], "normal")
        self.assertFalse(any(name.startswith(("margin", "padding", "border")) for name in values))
        emphasis, emphasis_values = declarations_for(output, "native-emphasis")
        self.assertEqual(emphasis.tag.rsplit("}", 1)[-1], "em")
        self.assertNotIn("font-style", emphasis_values)

    def test_translation_scope_wrapper_does_not_enlarge_its_paragraphs_twice(self):
        _, output, _ = self.styled(
            '<div id="wrapper" data-bilingual="target"><p id="primary">中文正文。</p></div>'
            '<div id="reference" data-bilingual="source"><p>English reference.</p></div>'
        )
        _, wrapper = declarations_for(output, "wrapper")
        _, primary = declarations_for(output, "primary")
        _, reference = declarations_for(output, "reference")
        self.assertNotIn("font-size", wrapper)
        self.assertNotIn("margin", wrapper)
        self.assertEqual(primary["font-size"], "1.15em")
        self.assertEqual(reference["font-size"], "0.943em")

    def test_explicit_mixed_paragraph_keeps_block_primary_and_inline_reference_flow(self):
        _, output, _ = self.styled(
            '<p id="primary" data-bilingual="target">中文正文'
            '<span id="reference" data-bilingual="source">English reference.</span></p>'
        )
        primary, values = declarations_for(output, "primary")
        self.assertEqual(primary.get("data-sy-content"), "mixed")
        self.assertEqual(values["font-size"], "1.15em")
        _, reference = declarations_for(output, "reference")
        self.assertEqual(reference["font-size"], "0.82em")
        self.assertNotIn("border-left", reference)


class BilingualIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        source = build_epub(
            self.directory / "source.epub",
            documents={
                "book.xhtml": document(
                    '<p id="primary">中文正文。</p>'
                    f'<p id="reference" style="{EXPORTER}">English reference.</p>'
                )
            },
        )
        self.book = load_book(source)
        self.inventory = build_inventory(self.book, bilingual=True)
        assignments = {
            node.node_id: Assignment(node.node_id, "body") for node in self.inventory.nodes
        }
        self.profile = LayoutProfile(
            self.book.source_sha256, self.inventory.digest, assignments, "offline-fixture"
        )
        self.plan = plan_theme(self.book, self.inventory, self.profile)
        self.sequence = 0

    def output(self, plan):
        self.sequence += 1
        output = self.directory / f"output-{self.sequence}.epub"
        write_book(self.book, plan.replacements, output)
        return output

    def test_identity_fields_and_digest_are_independently_recomputed(self):
        binding = next(item for item in self.plan.bindings if item.content == "source")
        for change in (
            {"content": "target"},
            {"content_root": False},
            {"pair_id": "forged"},
            {"flow": "inline"},
            {"exporter": False},
            {"source_sha256": "forged"},
        ):
            forged = replace(
                self.plan,
                bindings=tuple(
                    replace(item, **change) if item == binding else item
                    for item in self.plan.bindings
                ),
            )
            with self.subTest(change=change), self.assertRaises(ShuyiError) as caught:
                verify_output(self.book, self.output(forged), forged)
            self.assertEqual(caught.exception.code, "verification_binding_invalid")
        candidate = next(item for item in self.inventory.nodes if item.content == "source")
        forged_inventory = replace(
            self.inventory,
            nodes=tuple(
                replace(item, pair_id="forged") if item == candidate else item
                for item in self.inventory.nodes
            ),
        )
        with self.assertRaises(ShuyiError):
            plan_theme(self.book, forged_inventory, self.profile)

    def test_opacity_cannot_escape_source_and_standalone_marker_shape_is_checked(self):
        target = next(item for item in self.plan.bindings if item.content == "target")
        css = self.plan.replacements[self.plan.asset_path]
        source = next(item for item in self.plan.bindings if item.content == "source")
        rules = parse_rules(css.decode(), generated=True)
        selector = next(rule.selector for rule in rules if target.node_id in rule.selector)
        forged = replace(
            self.plan,
            replacements={
                **self.plan.replacements,
                self.plan.asset_path: css + f"{selector}{{opacity:1!important}}".encode(),
            },
        )
        with self.assertRaises(ShuyiError):
            verify_output(self.book, self.output(forged), forged)
        with self.assertRaises(ShuyiError):
            check_book(load_book(self.output(forged)))
        root = parse_xml(self.plan.replacements[source.resource], source.resource)
        resolve_element(root, source.path).set("data-sy-content", "unpaired")
        forged = replace(
            self.plan,
            replacements={
                **self.plan.replacements,
                source.resource: serialize_xml(root),
            },
        )
        with self.assertRaises(ShuyiError) as caught:
            check_book(load_book(self.output(forged)))
        self.assertEqual(caught.exception.code, "generated_binding_invalid")

    def test_exporter_inline_values_are_preserved_and_forged_changes_rejected(self):
        binding = next(item for item in self.plan.bindings if item.content == "source")
        original_root = parse_xml(self.book.entries[binding.resource], binding.resource)
        original = resolve_element(original_root, binding.path).get("style")
        root = parse_xml(self.plan.replacements[binding.resource], binding.resource)
        self.assertEqual(resolve_element(root, binding.path).get("style"), original)
        verify_output(self.book, self.output(self.plan), self.plan)
        resolve_element(root, binding.path).set("style", "opacity:1")
        forged = replace(
            self.plan,
            replacements={
                **self.plan.replacements,
                binding.resource: serialize_xml(root),
            },
        )
        with self.assertRaises(ShuyiError):
            verify_output(self.book, self.output(forged), forged)

    def test_generated_opacity_cannot_override_author_opacity_on_non_exporter_source(self):
        book = load_book(
            build_epub(
                self.directory / "author.epub",
                documents={
                    "book.xhtml": document(
                        '<p lang="zh">中文正文。</p>'
                        '<p lang="en" style="opacity:.4">Author reference.</p>'
                    )
                },
            )
        )
        inventory = build_inventory(book, bilingual=True)
        profile = LayoutProfile(
            book.source_sha256,
            inventory.digest,
            {n.node_id: Assignment(n.node_id, "body") for n in inventory.nodes},
            "offline-fixture",
        )
        plan = plan_theme(book, inventory, profile)
        source = next(binding for binding in plan.bindings if binding.content == "source")
        rules = parse_rules(plan.replacements[plan.asset_path].decode(), generated=True)
        selector = next(rule.selector for rule in rules if source.node_id in rule.selector)
        forged = replace(
            plan,
            replacements={
                **plan.replacements,
                plan.asset_path: plan.replacements[plan.asset_path]
                + f"{selector}{{opacity:1!important}}".encode(),
            },
        )
        output = self.directory / "author-forged.epub"
        write_book(book, forged.replacements, output)
        with self.assertRaises(ShuyiError):
            verify_output(book, output, forged)
        with self.assertRaises(ShuyiError):
            check_book(load_book(output))
