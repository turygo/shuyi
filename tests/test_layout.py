from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from shuyi.contracts import Assignment, ClassificationBatch, Inventory, LayoutNode, ShuyiError
from shuyi.epub.archive import load_book
from shuyi.epub.dom import node_digest, parse_xml, resolve_element
from shuyi.layout.analysis import analyze
from shuyi.layout.cache import exclusive
from shuyi.layout.inventory import build_inventory
from shuyi.layout.protocol import VERSION, canonical, fingerprint, request_size
from shuyi.layout.requests import chunks
from tests.fixtures import build_epub


def make_node(index: int, *, group: str = "same", text: str = "Text") -> LayoutNode:
    return LayoutNode(
        f"n{index:06d}",
        "chapter.xhtml",
        (index,),
        f"digest-{index}",
        "p",
        text,
        f"<p>{text}</p>",
        "",
        "[]",
        "",
        group,
    )


def make_inventory(nodes: list[LayoutNode]) -> Inventory:
    source = "a" * 64
    digest = fingerprint(
        {"version": VERSION, "source_sha256": source, "nodes": [asdict(n) for n in nodes]}
    )
    return Inventory(source, digest, tuple(nodes))


class RecordingClassifier:
    provenance = "test-model:first"

    def __init__(self, roles=None, fail_after: int | None = None):
        self.calls = []
        self.roles = roles or (lambda node: ("body", None))
        self.fail_after = fail_after

    def classify(self, samples):
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise ShuyiError("model_request_failed")
        self.calls.append(tuple(samples))
        return ClassificationBatch(
            tuple(Assignment(n.node_id, *self.roles(n)) for n in samples),
            {"total_tokens": len(samples)},
        )


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = Path(self.temporary.name)

    def test_small_group_classifies_every_node(self):
        inventory = make_inventory([make_node(i) for i in range(1, 7)])
        classifier = RecordingClassifier()
        profile = analyze(inventory, classifier, self.cache)
        self.assertEqual(sum(map(len, classifier.calls)), 6)
        self.assertEqual(set(profile.assignments), {n.node_id for n in inventory.nodes})
        self.assertEqual(profile.usage, {"total_tokens": 6})

    def test_large_group_samples_held_out_then_propagates(self):
        inventory = make_inventory([make_node(i) for i in range(1, 31)])
        classifier = RecordingClassifier()
        profile = analyze(inventory, classifier, self.cache)
        self.assertEqual([len(call) for call in classifier.calls], [3, 3])
        self.assertEqual(len(profile.assignments), 30)
        self.assertEqual({a.role for a in profile.assignments.values()}, {"body"})

    def test_held_out_conflict_expands_every_remaining_node(self):
        inventory = make_inventory([make_node(i) for i in range(1, 21)])
        classifier = RecordingClassifier(
            lambda n: ("heading", 2) if n.node_id == "n000002" else ("body", None)
        )
        profile = analyze(inventory, classifier, self.cache)
        self.assertEqual(sum(map(len, classifier.calls)), 20)
        self.assertEqual(profile.assignments["n000002"].role, "heading")
        self.assertTrue(
            all(len(batch) <= 16 and request_size(batch) <= 24000 for batch in classifier.calls)
        )

    def test_unknown_never_propagates(self):
        inventory = make_inventory([make_node(i) for i in range(1, 20)])
        classifier = RecordingClassifier(lambda _: (None, None))
        profile = analyze(inventory, classifier, self.cache)
        self.assertEqual(sum(map(len, classifier.calls)), 19)
        self.assertTrue(all(a.role is None for a in profile.assignments.values()))

    def test_cache_avoids_model_and_keeps_original_provenance(self):
        inventory = make_inventory([make_node(1)])
        first = analyze(inventory, RecordingClassifier(), self.cache)
        unused = RecordingClassifier(fail_after=0)
        unused.provenance = "test-model:replacement"
        second = analyze(inventory, unused, self.cache)
        self.assertEqual(first.assignments, second.assignments)
        self.assertEqual(second.provenance, "test-model:first")
        self.assertEqual(second.cache_hits, 1)
        self.assertEqual(unused.calls, [])

    def test_partial_checkpoint_resumes_without_repeating_calls(self):
        inventory = make_inventory([make_node(i, group=str(i)) for i in range(1, 5)])
        interrupted = RecordingClassifier(fail_after=2)
        with self.assertRaisesRegex(ShuyiError, "model_request_failed"):
            analyze(inventory, interrupted, self.cache)
        resumed = RecordingClassifier()
        profile = analyze(inventory, resumed, self.cache)
        self.assertEqual(sum(map(len, resumed.calls)), 2)
        self.assertEqual(profile.usage["total_tokens"], 4)
        self.assertEqual(profile.cache_hits, 2)

    def test_failed_refresh_preserves_accepted_profile(self):
        inventory = make_inventory([make_node(1), make_node(2)])
        first = analyze(inventory, RecordingClassifier(), self.cache)
        with self.assertRaises(ShuyiError):
            analyze(inventory, RecordingClassifier(fail_after=0), self.cache, reanalyze=True)
        cached = analyze(inventory, RecordingClassifier(fail_after=0), self.cache)
        self.assertEqual(cached.assignments, first.assignments)
        refreshed = analyze(
            inventory, RecordingClassifier(lambda _: ("heading", 1)), self.cache, reanalyze=True
        )
        self.assertEqual({a.role for a in refreshed.assignments.values()}, {"heading"})

    def test_interrupted_refresh_resumes_only_unfinished_paid_batches(self):
        inventory = make_inventory([make_node(i, group=str(i)) for i in range(1, 5)])
        original = analyze(inventory, RecordingClassifier(), self.cache)
        interrupted = RecordingClassifier(lambda _: ("heading", 2), fail_after=2)
        with self.assertRaisesRegex(ShuyiError, "model_request_failed"):
            analyze(inventory, interrupted, self.cache, reanalyze=True)
        cached = analyze(inventory, RecordingClassifier(fail_after=0), self.cache)
        self.assertEqual(cached.assignments, original.assignments)
        resumed = RecordingClassifier(lambda _: ("heading", 2))
        profile = analyze(inventory, resumed, self.cache, reanalyze=True)
        self.assertEqual(sum(map(len, resumed.calls)), 2)
        self.assertEqual({item.role for item in profile.assignments.values()}, {"heading"})
        self.assertEqual(profile.usage["total_tokens"], 4)

    def test_corrupt_cache_fails_without_paid_restart(self):
        inventory = make_inventory([make_node(1)])
        analyze(inventory, RecordingClassifier(), self.cache)
        accepted = next(self.cache.glob("*/accepted.json"))
        accepted.write_text('{"source_sha256":"bad"}', encoding="utf-8")
        classifier = RecordingClassifier()
        with self.assertRaisesRegex(ShuyiError, "cache_invalid"):
            analyze(inventory, classifier, self.cache)
        self.assertEqual(classifier.calls, [])

    def test_tampered_checkpoint_binding_and_profile_fail(self):
        inventory = make_inventory([make_node(1)])
        analyze(inventory, RecordingClassifier(), self.cache)
        accepted = next(self.cache.glob("*/accepted.json"))
        original = json.loads(accepted.read_text())
        for mutation in ("binding", "profile", "source"):
            value = json.loads(canonical(original))
            if mutation == "binding":
                value["records"][0]["binding"] = "wrong"
            elif mutation == "profile":
                value["assignments"][0]["role"] = "quote"
            else:
                value["nodes"]["n000001"] = "wrong"
            accepted.write_text(canonical(value))
            with self.assertRaisesRegex(ShuyiError, "cache_invalid"):
                analyze(inventory, RecordingClassifier(fail_after=0), self.cache)

    def test_long_own_evidence_is_complete_and_chunks_agree(self):
        node = make_node(1, text='A"\\中文' * 7000)
        parts = chunks(node)
        self.assertGreater(len(parts), 1)
        self.assertEqual(
            "".join(part.text for part in parts),
            canonical({"text": node.text, "markup": node.markup}),
        )
        self.assertTrue(
            all(len(canonical({"text": part.text, "markup": ""})) <= 8000 for part in parts)
        )
        classifier = RecordingClassifier()
        profile = analyze(make_inventory([node]), classifier, self.cache)
        self.assertEqual(profile.assignments[node.node_id].role, "body")
        self.assertTrue(all(request_size(batch) <= 24000 for batch in classifier.calls))

    def test_chunk_disagreement_becomes_unknown_and_partial_resume_works(self):
        inventory = make_inventory([make_node(1, text="x" * 80000)])
        with self.assertRaises(ShuyiError):
            analyze(inventory, RecordingClassifier(fail_after=1), self.cache)
        resumed = RecordingClassifier(lambda _: ("quote", None))
        profile = analyze(inventory, resumed, self.cache)
        observed_ids = {n.node_id for batch in resumed.calls for n in batch}
        self.assertNotIn("n000001:c0", observed_ids)
        self.assertIsNone(profile.assignments["n000001"].role)

    def test_context_limit_does_not_silently_truncate(self):
        node = replace(make_node(1), context="x" * 25000)
        with self.assertRaisesRegex(ShuyiError, "model_context_limit"):
            analyze(make_inventory([node]), RecordingClassifier(), self.cache)

    def test_injected_classifier_results_are_strictly_validated(self):
        inventory = make_inventory([make_node(1)])
        for role, level in (("bad", None), ("heading", True), ("body", 1), (None, 1)):
            with (
                self.subTest(role=role, level=level),
                self.assertRaisesRegex(ShuyiError, "model_response_invalid"),
            ):
                analyze(
                    inventory,
                    RecordingClassifier(lambda _, role=role, level=level: (role, level)),
                    self.cache,
                )

    def test_exclusive_source_lock_has_explicit_busy_error(self):
        directory = self.cache / "lock"
        with exclusive(directory):
            with self.assertRaisesRegex(ShuyiError, "cache_busy"):
                with exclusive(directory):
                    self.fail("second lock acquired")


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def book(self, body, *, stylesheet="p { color: black; }", protected=()):
        document = (
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Test</title>'
            '<link rel="stylesheet" href="styles.css"/></head><body>' + body + "</body></html>"
        )
        return load_book(
            build_epub(
                self.directory / "source.epub",
                documents={"book.xhtml": document},
                stylesheet=stylesheet,
                protected=protected,
            )
        )

    def test_native_and_paragraph_only_candidates_preserve_full_evidence(self):
        book = self.book(
            '<h1>Native heading</h1><p class="chapter">Paragraph heading</p>'
            "<p>Body <em>emphasis</em></p><ul><li>List item</li></ul>"
            "<blockquote>Quote</blockquote><table><tr><td>Cell</td></tr></table>"
            "<dl><dt>Term</dt><dd>Definition</dd></dl>"
            "<figure><figcaption>Caption</figcaption></figure>"
            "<div>Direct text <span>with span</span></div><p> </p>"
        )
        original = dict(book.entries)
        inventory = build_inventory(book)
        self.assertEqual(
            [n.tag for n in inventory.nodes],
            ["h1", "p", "p", "li", "blockquote", "td", "dt", "dd", "figcaption", "div"],
        )
        self.assertEqual(inventory.nodes[1].text, "Paragraph heading")
        self.assertIn("<em", inventory.nodes[2].markup)
        self.assertTrue(all(len(n.context) <= 1000 for n in inventory.nodes))
        for node in inventory.nodes:
            root = parse_xml(book.entries[node.resource], node.resource)
            self.assertEqual(node.source_sha256, node_digest(resolve_element(root, node.path)))
        self.assertEqual(book.entries, original)
        self.assertEqual(inventory, build_inventory(book))

    def test_css_nth_child_inline_and_ancestor_features_split_groups(self):
        book = self.book(
            '<p class="same">One</p><p class="same">Two</p>'
            '<p class="same" style="font-weight:bold">Three</p>'
            '<div role="note"><p class="same">Four</p></div>',
            stylesheet="p { color: black; } p:nth-child(2) { text-align:center; }",
        )
        nodes = build_inventory(book).nodes
        self.assertEqual(len(nodes), 4)
        self.assertNotEqual(nodes[0].group_key, nodes[1].group_key)
        self.assertNotEqual(nodes[0].group_key, nodes[2].group_key)
        self.assertNotEqual(nodes[0].group_key, nodes[3].group_key)
        self.assertIn("font-weight", nodes[2].source_css)

    def test_protected_subtrees_and_their_parent_candidates_are_excluded(self):
        book = self.book(
            "<nav><p>Navigation</p></nav><pre>Code</pre>"
            "<div>Parent with <code>protected code</code></div>"
            "<p>Safe text</p><p>Ruby <ruby>字<rt>zi</rt></ruby></p>"
        )
        inventory = build_inventory(book)
        self.assertEqual([n.text for n in inventory.nodes], ["Safe text"])

    def test_fully_protected_document_has_no_candidates(self):
        book = self.book("<p>Title page</p>", protected=("book.xhtml",))
        self.assertEqual(build_inventory(book).nodes, ())
