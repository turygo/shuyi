from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from shuyi.contracts import Assignment, AttributeEdit, LayoutProfile, ShuyiError, TextEdit
from shuyi.epub.archive import load_book, write_book
from shuyi.epub.dom import element_path, parse_xml, resolve_element, serialize_xml
from shuyi.layout.inventory import build_inventory
from shuyi.theme.service import plan_theme
from shuyi.verify.preservation import check_book, verify_output
from tests.fixtures import build_epub
from tests.test_epub import xhtml


class PreservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.sequence = 0

    def planned(
        self, *, version="3.0", body='<p id="original">Original prose <em>emphasis</em>.</p>'
    ):
        source = build_epub(
            self.directory / "source.epub",
            version=version,
            documents={"book.xhtml": xhtml(body)},
            extra_entries={"unchanged.bin": b"\x00\xffbinary"},
        )
        book = load_book(source)
        inventory = build_inventory(book)
        assignments = {node.node_id: Assignment(node.node_id, "body") for node in inventory.nodes}
        profile = LayoutProfile(book.source_sha256, inventory.digest, assignments, "offline-test")
        return book, plan_theme(book, inventory, profile)

    def output(self, book, plan):
        self.sequence += 1
        output = self.directory / f"output-{self.sequence}.epub"
        write_book(book, plan.replacements, output)
        return output

    def changed_document(self, plan, resource, change):
        root = parse_xml(plan.replacements[resource], resource)
        change(root)
        return replace(plan, replacements={**plan.replacements, resource: serialize_xml(root)})

    def test_epub2_and_3_verify_real_theme_outputs(self):
        for version in ("2.0", "3.0"):
            with self.subTest(version=version):
                if (self.directory / "source.epub").exists():
                    (self.directory / "source.epub").unlink()
                book, plan = self.planned(version=version)
                destination = self.output(book, plan)
                report = verify_output(book, destination, plan)
                self.assertEqual(report["source_sha256"], book.source_sha256)
                self.assertEqual(report["styled_nodes"], len(plan.bindings))
                output = load_book(destination)
                self.assertEqual(output.entries["unchanged.bin"], book.entries["unchanged.bin"])
                self.assertTrue(check_book(output)["generated_theme"])

    def test_exact_output_must_equal_plan(self):
        book, plan = self.planned()
        resource = book.documents[0]
        forged = replace(
            plan,
            replacements={
                **plan.replacements,
                resource: plan.replacements[resource].replace(b"Original prose", b"Altered prose"),
            },
        )
        with self.assertRaises(ShuyiError):
            verify_output(book, self.output(book, forged), plan)

    def test_forged_plan_cannot_authorize_prose_or_tail_changes(self):
        book, plan = self.planned()
        binding = plan.bindings[0]
        root = parse_xml(plan.replacements[binding.resource], binding.resource)
        node = resolve_element(root, binding.path)
        before = node.text
        node.text = "Forged prose."
        forged = replace(
            plan,
            replacements={**plan.replacements, binding.resource: serialize_xml(root)},
            texts=(
                *plan.texts,
                TextEdit(binding.resource, binding.path, "text", before, node.text),
            ),
        )
        with self.assertRaises(ShuyiError):
            verify_output(book, self.output(book, forged), forged)
        forged = self.changed_document(
            plan,
            binding.resource,
            lambda root: setattr(resolve_element(root, binding.path), "tail", "Forged tail."),
        )
        with self.assertRaises(ShuyiError):
            verify_output(book, self.output(book, forged), forged)

    def test_forged_href_attribute_is_not_a_presentation_edit(self):
        book, plan = self.planned(
            body='<p><a href="#target">Original link</a></p><p id="target">Target.</p>'
        )
        resource = book.documents[0]
        root = parse_xml(plan.replacements[resource], resource)
        anchor = root.find(".//{http://www.w3.org/1999/xhtml}a")
        path = element_path(root, anchor)
        anchor.set("href", "https://example.com/changed")
        forged = replace(
            plan,
            replacements={**plan.replacements, resource: serialize_xml(root)},
            attributes=(
                *plan.attributes,
                AttributeEdit(resource, path, "href", "#target", anchor.get("href")),
            ),
        )
        with self.assertRaises(ShuyiError):
            verify_output(book, self.output(book, forged), forged)

    def test_forged_resource_change_and_added_member_fail(self):
        book, plan = self.planned()
        for name in ("unchanged.bin", "unexpected.bin"):
            forged = replace(plan, replacements={**plan.replacements, name: b"changed"})
            with self.subTest(name=name), self.assertRaises(ShuyiError):
                verify_output(book, self.output(book, forged), forged)

    def test_broadened_css_is_rejected_even_when_plan_authorizes_bytes(self):
        book, plan = self.planned()
        forged = replace(
            plan,
            replacements={**plan.replacements, plan.asset_path: b"p { color: red !important; }"},
        )
        destination = self.output(book, forged)
        with self.assertRaises(ShuyiError):
            verify_output(book, destination, forged)
        with self.assertRaises(ShuyiError):
            check_book(load_book(destination))

    def test_standalone_rejects_malformed_or_network_generated_assets(self):
        book, plan = self.planned()
        for data in (
            b'@import "https://example.com/style.css";',
            b'[data-sy-node="n000001"] { background-image: url(https://example.com/image); }',
            b"p { broken",
        ):
            forged = replace(plan, replacements={**plan.replacements, plan.asset_path: data})
            with self.subTest(data=data), self.assertRaises(ShuyiError):
                check_book(load_book(self.output(book, forged)))

    def test_comments_and_processing_instructions_are_immutable(self):
        book, plan = self.planned()
        resource = book.documents[0]
        for original, changed in (
            (b"<!--keep-->", b"<!--altered-->"),
            (b"<?outside preserved?>", b"<?outside altered?>"),
        ):
            forged = replace(
                plan,
                replacements={
                    **plan.replacements,
                    resource: plan.replacements[resource].replace(original, changed),
                },
            )
            with self.subTest(original=original), self.assertRaises(ShuyiError):
                verify_output(book, self.output(book, forged), forged)

    def test_notes_rewrite_owned_labels_but_preserve_nested_structure_and_outer_tail(self):
        body = (
            '<p>Before <a id="ref" href="#note" epub:type="noteref"><sup>1</sup></a> after.</p>'
            '<aside id="note" epub:type="footnote"><a href="#ref">↩</a> Note prose.</aside>'
        )
        book, plan = self.planned(body=body)
        destination = self.output(book, plan)
        verify_output(book, destination, plan)
        resource = book.documents[0]
        root = parse_xml(load_book(destination).entries[resource], resource)
        anchor = root.find(".//{http://www.w3.org/1999/xhtml}a")
        self.assertEqual("".join(anchor.itertext()), "注")
        self.assertEqual(anchor.tail, " after.")
        self.assertEqual(anchor.get("id"), "ref")
        self.assertEqual(anchor.get("href"), "#note")
        self.assertIsNotNone(anchor.find("{http://www.w3.org/1999/xhtml}sup"))
        self.assertEqual(plan.note_count, 2)

    def test_unlisted_reserved_attribute_cannot_be_smuggled_into_plan(self):
        book, plan = self.planned()
        binding = plan.bindings[0]
        root = parse_xml(plan.replacements[binding.resource], binding.resource)
        resolve_element(root, binding.path).set("data-sy-anything", "yes")
        forged = replace(
            plan,
            replacements={**plan.replacements, binding.resource: serialize_xml(root)},
            attributes=(
                *plan.attributes,
                AttributeEdit(binding.resource, binding.path, "data-sy-anything", None, "yes"),
            ),
        )
        with self.assertRaises(ShuyiError):
            verify_output(book, self.output(book, forged), forged)
