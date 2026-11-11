from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from lxml import etree
from typer.testing import CliRunner

from shuyi import application
from shuyi.cli import app
from shuyi.contracts import ShuyiError
from tests.fixtures import FakeClassifier, build_epub, document


class TestApplication(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"
        self.classifier = FakeClassifier()

    def test_cli_epub2_and_epub3_preserve_content_and_binary_resources(self) -> None:
        for version in ("2.0", "3.0"):
            with self.subTest(version=version):
                source = build_epub(
                    self.root / f"source-{version}.epub",
                    version=version,
                    documents={
                        "book.xhtml": document(
                            '<h1 id="chapter">第一章</h1>'
                            '<p>这是正文，包含<strong>强调</strong>。</p><img src="image.png"/>'
                        )
                    },
                    extra_entries={"image.png": b"binary-image-\x00\xff"},
                )
                original = source.read_bytes()
                with (
                    patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache)}),
                    patch("shuyi.application.configured_classifier", return_value=self.classifier),
                ):
                    result = CliRunner().invoke(app, ["style", str(source)])
                self.assertEqual(result.exit_code, 0, result.output)
                output = source.with_name(f"{source.stem}.shuyi.epub")
                self.assertEqual(source.read_bytes(), original)
                with zipfile.ZipFile(source) as before, zipfile.ZipFile(output) as after:
                    self.assertEqual(after.read("image.png"), before.read("image.png"))
                    self.assertEqual(after.read("styles.css"), before.read("styles.css"))
                    root = etree.fromstring(after.read("book.xhtml"))
                    body = root.find("{http://www.w3.org/1999/xhtml}body")
                    self.assertEqual("".join(body.itertext()), "第一章这是正文，包含强调。")
                    self.assertEqual(body[0].get("id"), "chapter")
                    self.assertEqual(body[0].get("data-sy-role"), "heading")
                    self.assertEqual(body[1].get("data-sy-role"), "body")
                    self.assertIn(b"font-size", after.read("shuyi/reading.css"))
                checked = CliRunner().invoke(app, ["check", str(output)])
                self.assertEqual(checked.exit_code, 0, checked.output)

    def test_retheme_uses_cache_with_no_credentials_or_extra_calls(self) -> None:
        source = build_epub(self.root / "book.epub")
        application.style(
            source, self.root / "first.epub", classifier=self.classifier, cache_dir=self.cache
        )
        calls = list(self.classifier.calls)
        css = self.root / "custom.css"
        css.write_text('[data-sy-role="body"] { line-height: 1.8; }', encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True):
            second = application.style(
                source, self.root / "second.epub", css_path=css, cache_dir=self.cache
            )
        self.assertEqual(self.classifier.calls, calls)
        self.assertGreater(second["cache_hits"], 0)
        self.assertEqual(second["analysis_provenance"], "offline-fixture")
        with zipfile.ZipFile(self.root / "second.epub") as archive:
            self.assertIn(b"1.8", archive.read("shuyi/reading.css"))

    def test_explicit_notes_become_zhu_but_keep_anchor_graph_and_prose(self) -> None:
        source = build_epub(
            self.root / "notes.epub",
            documents={
                "book.xhtml": document(
                    '<p>正文<a id="ref" href="#note" epub:type="noteref"><sup>1</sup></a>继续。</p>'
                    '<aside id="note" epub:type="footnote"><p>'
                    '<a href="#ref" role="doc-backlink">↩</a>注释正文不变。</p></aside>'
                )
            },
        )
        result = application.style(source, classifier=self.classifier, cache_dir=self.cache)
        with zipfile.ZipFile(result["output"]) as archive:
            root = etree.fromstring(archive.read("book.xhtml"))
            anchors = root.xpath('//*[local-name()="a"]')
            self.assertEqual(["".join(a.itertext()) for a in anchors], ["注", "注"])
            self.assertEqual([a.get("href") for a in anchors], ["#note", "#ref"])
            self.assertEqual(anchors[0].get("id"), "ref")
            self.assertEqual(anchors[0].tail, "继续。")
            self.assertEqual(anchors[1].tail, "注释正文不变。")
            self.assertEqual(len(anchors[0]), 1)
        self.assertEqual(result["note_markers"], 2)

    def test_existing_destination_and_aliases_never_change_source(self) -> None:
        source = build_epub(self.root / "book.epub")
        original = source.read_bytes()
        hardlink = self.root / "hardlink.epub"
        os.link(source, hardlink)
        dangling = self.root / "dangling.epub"
        dangling.symlink_to(self.root / "missing.epub")
        for output in (source, hardlink, dangling):
            with self.subTest(output=output.name), self.assertRaises(ShuyiError):
                application.style(source, output, classifier=self.classifier, cache_dir=self.cache)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(self.classifier.calls, [])

    def test_verification_failure_never_publishes_or_leaves_temp_files(self) -> None:
        source = build_epub(self.root / "book.epub")
        destination = self.root / "failed.epub"
        with patch("shuyi.application.verify_output", side_effect=ShuyiError("verify_failed")):
            with self.assertRaisesRegex(ShuyiError, "verify_failed"):
                application.style(
                    source, destination, classifier=self.classifier, cache_dir=self.cache
                )
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".shuyi-*")), [])

    def test_publish_race_keeps_competing_destination(self) -> None:
        source = build_epub(self.root / "book.epub")
        destination = self.root / "raced.epub"
        real_verify = application.verify_output

        def racing_verify(*args):
            report = real_verify(*args)
            destination.write_bytes(b"competing owner")
            return report

        with patch("shuyi.application.verify_output", side_effect=racing_verify):
            with self.assertRaisesRegex(ShuyiError, "destination_exists"):
                application.style(
                    source, destination, classifier=self.classifier, cache_dir=self.cache
                )
        self.assertEqual(destination.read_bytes(), b"competing owner")

    def test_inspect_is_offline_and_missing_model_does_not_publish(self) -> None:
        source = build_epub(self.root / "book.epub")
        with patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": str(self.root / "config"), "XDG_CACHE_HOME": str(self.cache)},
            clear=True,
        ):
            inspected = CliRunner().invoke(app, ["inspect", str(source)])
            self.assertEqual(inspected.exit_code, 0, inspected.output)
            self.assertEqual(json.loads(inspected.stdout)["candidates"], 2)
            styled = CliRunner().invoke(app, ["style", str(source)])
        self.assertEqual(styled.exit_code, 2)
        self.assertIn("model_config_missing", styled.output)
        self.assertFalse(source.with_name("book.shuyi.epub").exists())

    def test_bilingual_cli_preserves_unpaired_content_and_cached_identity(self) -> None:
        source = build_epub(
            self.root / "parallel.epub",
            documents={
                "book.xhtml": document(
                    '<h1 data-bilingual="target">双语标题</h1>'
                    '<h1 data-bilingual="source">Parallel heading</h1>'
                    '<p data-bilingual="target">正文包含<em>原有强调</em>。</p>'
                    '<p data-bilingual="source">Prose with <em>original emphasis</em>.</p>'
                    '<p lang="en">Independent bibliography entry.</p>'
                )
            },
        )
        with (
            patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache)}),
            patch("shuyi.application.configured_classifier", return_value=self.classifier),
        ):
            result = CliRunner().invoke(app, ["style", str(source), "--bilingual"])
        self.assertEqual(result.exit_code, 0, result.output)
        report = json.loads(result.stdout)
        self.assertTrue(report["bilingual"])
        self.assertEqual(report["content_counts"], {"target": 2, "source": 2})
        self.assertEqual(report["unpaired_nodes"], 1)
        with zipfile.ZipFile(report["output"]) as archive:
            root = etree.fromstring(archive.read("book.xhtml"))
            content = root.xpath('//*[local-name()="body"]/*')
            self.assertEqual(
                [node.get("data-sy-content") for node in content],
                ["target", "source", "target", "source", None],
            )
            self.assertEqual(dict(content[-1].attrib), {"lang": "en"})
            self.assertEqual(content[3][0].text, "original emphasis")
        calls = list(self.classifier.calls)
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "no-config")}, clear=True):
            cached = application.style(
                source,
                self.root / "parallel-cached.epub",
                bilingual=True,
                cache_dir=self.cache / "shuyi",
            )
        self.assertEqual(self.classifier.calls, calls)
        self.assertEqual(cached["content_counts"], report["content_counts"])
        self.assertGreater(cached["cache_hits"], 0)
        self.assertTrue(application.check(Path(cached["output"]))["generated_theme"])

    def test_bilingual_conflict_is_reported_offline_and_never_published(self) -> None:
        source = build_epub(
            self.root / "conflict.epub",
            documents={
                "book.xhtml": document(
                    '<p data-bilingual="target">中文译文。</p>'
                    '<p data-bilingual="source" lang="zh">Original English.</p>'
                )
            },
        )
        inspected = CliRunner().invoke(app, ["inspect", str(source)])
        self.assertEqual(inspected.exit_code, 0, inspected.output)
        issues = json.loads(inspected.stdout)["bilingual"]["issues"]
        conflict = next(issue for issue in issues if issue["code"] == "bilingual_language_conflict")
        self.assertEqual(conflict["resource"], "book.xhtml")
        with patch("shuyi.application.configured_classifier", return_value=self.classifier):
            styled = CliRunner().invoke(app, ["style", str(source), "--bilingual"])
        self.assertEqual(styled.exit_code, 2)
        self.assertIn("bilingual_language_conflict", styled.output)
        self.assertNotIn("Original English", styled.output)
        self.assertEqual(self.classifier.calls, [])
        self.assertFalse(source.with_name("conflict.shuyi.epub").exists())

    def test_uncertain_exporter_bibliography_stays_visible_and_is_reported(self) -> None:
        signature = "opacity:.8;color:#888;font-size:.85em;font-style:italic;margin-top:.2em"
        source = build_epub(
            self.root / "bibliography.epub",
            documents={
                "book.xhtml": document(
                    "<p>中文正文。</p>"
                    f'<p style="{signature}">English reference.</p>'
                    '<h2 id="letter">A</h2>'
                    f'<h2 id="uncertain" style="{signature}">A</h2>'
                )
            },
        )
        report = application.style(
            source, bilingual=True, classifier=self.classifier, cache_dir=self.cache
        )
        self.assertTrue(
            any(
                warning.startswith("bilingual_unpaired_reference:book.xhtml:")
                for warning in report["warnings"]
            )
        )
        with zipfile.ZipFile(report["output"]) as archive:
            root = etree.fromstring(archive.read("book.xhtml"))
            for identity in ("letter", "uncertain"):
                node = root.xpath("//*[@id=$identity]", identity=identity)[0]
                self.assertIsNone(node.get("data-sy-content"))
                self.assertEqual(node.text, "A")
            self.assertEqual(root.xpath('//*[@id="uncertain"]')[0].get("style"), signature)

    def test_epubcheck_unavailable_or_failed_is_an_error(self) -> None:
        source = build_epub(self.root / "book.epub")
        with patch("shuyi.application.shutil.which", return_value=None):
            with self.assertRaisesRegex(ShuyiError, "epubcheck_unavailable"):
                application.check(source, epubcheck=True)
        with (
            patch("shuyi.application.shutil.which", return_value="epubcheck"),
            patch("shuyi.application.subprocess.run") as command,
        ):
            command.return_value.returncode = 1
            with self.assertRaisesRegex(ShuyiError, "epubcheck_failed"):
                application.check(source, epubcheck=True)
