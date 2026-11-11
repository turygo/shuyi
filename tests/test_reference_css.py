from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from shuyi.application import style
from shuyi.contracts import AttributeEdit, LayoutProfile, ShuyiError
from shuyi.epub.archive import load_book, write_book
from shuyi.epub.dom import element_path, parse_xml, resolve_element, serialize_xml
from shuyi.epub.styles import matching_rules, parse_rules
from shuyi.layout.inventory import build_inventory
from shuyi.theme.service import plan_theme
from shuyi.verify.preservation import check_book, verify_output
from tests.fixtures import FakeClassifier, build_epub, document

EXPORTER = "opacity:.8;color:#888;font-size:.85em;font-style:italic;margin-top:.2em"
RESET = {
    "font-size": "1em",
    "line-height": "inherit",
    "float": "none",
    "margin": "0",
    "padding": "0",
}


class ReferenceFirstLetterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.source = build_epub(
            self.directory / "source.epub",
            documents={
                "book.xhtml": document(
                    '<p id="primary" class="dropcap">中文正文。</p>'
                    f'<p id="reference" class="dropcap" style="float:left!important;{EXPORTER}">'
                    "English <em>emphasis</em>.</p>"
                )
            },
            stylesheet=".dropcap::first-letter{font-size:3em;line-height:0;float:left;margin:1em}",
        )
        self.book = load_book(self.source)
        inventory = build_inventory(self.book, bilingual=True)
        observations = FakeClassifier().classify(inventory.nodes).observations
        profile = LayoutProfile(
            self.book.source_sha256,
            inventory.digest,
            {item.node_id: item for item in observations},
            "offline-fixture",
        )
        self.plan = plan_theme(self.book, inventory, profile)
        self.rules = parse_rules(
            self.plan.replacements[self.plan.asset_path].decode(), generated=True
        )
        self.reference = next(b for b in self.plan.bindings if b.content == "source")
        self.primary = next(b for b in self.plan.bindings if b.content == "target")
        self.sequence = 0

    def output(self, plan):
        self.sequence += 1
        destination = self.directory / f"output-{self.sequence}.epub"
        write_book(self.book, plan.replacements, destination)
        return destination

    def test_reference_dropcap_reset_does_not_change_primary_or_parent_typography(self):
        report = style(
            self.source,
            self.directory / "styled.epub",
            bilingual=True,
            classifier=FakeClassifier(),
            cache_dir=self.directory / "cache",
        )
        output = load_book(Path(report["output"]))
        rules = parse_rules(output.entries[self.plan.asset_path].decode(), generated=True)
        resets = [rule for rule in rules if rule.selector.endswith("::first-letter")]
        self.assertEqual(len(resets), 1)
        self.assertIn(self.reference.node_id, resets[0].selector)
        self.assertNotIn(self.primary.node_id, resets[0].selector)
        self.assertEqual({name: value for name, value, _ in resets[0].declarations}, RESET)
        root = parse_xml(output.entries["book.xhtml"], "book.xhtml")
        source = root.xpath('//*[@id="reference"]')[0]
        primary = root.xpath('//*[@id="primary"]')[0]
        matches = matching_rules(root, rules)

        def values(node):
            return {
                name: value
                for rule in matches[element_path(root, node)]
                if not rule.condition
                for name, value, _ in rule.declarations
            }

        self.assertEqual(values(source)["font-size"], "0.943em")
        self.assertNotIn("float", values(source))
        self.assertEqual(values(primary)["font-size"], "1.15em")
        self.assertEqual(source[0].tag.rsplit("}", 1)[-1], "em")
        self.assertEqual(output.entries["styles.css"], self.book.entries["styles.css"])
        self.assertTrue(check_book(output)["generated_theme"])

    def test_closed_pseudo_exception_rejects_primary_and_arbitrary_values(self):
        source_selector = next(
            rule.selector for rule in self.rules if rule.selector.endswith("::first-letter")
        )
        for selector, changes in (
            (source_selector.replace(self.reference.node_id, self.primary.node_id), {}),
            (source_selector, {"font-size": "3em"}),
            (source_selector, {"float": "left"}),
            (source_selector, {"content": '"injected"'}),
        ):
            values = {**RESET, **changes}
            css = (
                selector
                + "{"
                + ";".join(f"{name}:{value}!important" for name, value in values.items())
                + "}"
            )
            forged = replace(
                self.plan,
                replacements={
                    **self.plan.replacements,
                    self.plan.asset_path: self.plan.replacements[self.plan.asset_path]
                    + css.encode(),
                },
            )
            destination = self.output(forged)
            with self.subTest(selector=selector, changes=changes), self.assertRaises(ShuyiError):
                verify_output(self.book, destination, forged)
            with self.assertRaises(ShuyiError):
                check_book(load_book(destination))

    def test_custom_css_cannot_request_the_internal_pseudo_operation(self):
        rule = next(rule for rule in self.rules if rule.selector.endswith("::first-letter"))
        css = (
            rule.selector
            + "{"
            + ";".join(f"{name}:{value}!important" for name, value in RESET.items())
            + "}"
        )
        with self.assertRaises(ShuyiError):
            parse_rules(css, custom=True)
        with self.assertRaises(ShuyiError):
            parse_rules(css.replace(rule.selector, "p::first-letter"), generated=True)

    def test_pseudo_reset_cannot_authorize_parent_inline_priority_demotion(self):
        original = resolve_element(
            parse_xml(self.book.entries["book.xhtml"], "book.xhtml"), self.reference.path
        ).get("style")
        changed = original.replace("float:left!important", "float:left")
        root = parse_xml(self.plan.replacements["book.xhtml"], "book.xhtml")
        resolve_element(root, self.reference.path).set("style", changed)
        forged = replace(
            self.plan,
            replacements={**self.plan.replacements, "book.xhtml": serialize_xml(root)},
            attributes=(
                *self.plan.attributes,
                AttributeEdit("book.xhtml", self.reference.path, "style", original, changed),
            ),
        )
        with self.assertRaises(ShuyiError):
            verify_output(self.book, self.output(forged), forged)
