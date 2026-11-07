from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from shuyi.contracts import ShuyiError
from shuyi.epub.archive import load_book, write_book
from shuyi.epub.dom import (
    canonical_document,
    element_path,
    is_protected,
    parse_xml,
    resolve_element,
    serialize_xml,
)
from shuyi.epub.notes import find_note_markers, label_fields
from shuyi.epub.package import resolve_href
from tests.fixtures import build_epub


def xhtml(body: str) -> str:
    return (
        '<?xml version="1.0"?><!DOCTYPE html><?outside preserved?>'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        "<head><title>Fixture</title></head><body><!--keep-->" + body + "</body></html>"
    )


class EpubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def book(self, body='<p id="one">Original prose.</p>', **kwargs):
        path = build_epub(
            self.directory / "source.epub", documents={"book.xhtml": xhtml(body)}, **kwargs
        )
        return load_book(path)

    def repack(self, book, changes, name="changed.epub"):
        path = self.directory / name
        write_book(book, changes, path)
        return path

    def test_epub2_and_3_roundtrip_keeps_every_resource(self):
        for version in ("2.0", "3.0"):
            with self.subTest(version=version):
                path = build_epub(
                    self.directory / f"source-{version}.epub",
                    version=version,
                    documents={"book.xhtml": xhtml("<p>Original.</p>")},
                    extra_entries={"binary.dat": b"\x00\xff\x80"},
                )
                book = load_book(path)
                destination = self.directory / f"output-{version}.epub"
                write_book(book, {}, destination)
                output = load_book(destination)
                self.assertEqual(book.entries, output.entries)
                self.assertEqual(book.version, output.version)
                with zipfile.ZipFile(destination) as archive:
                    self.assertEqual(archive.infolist()[0].filename, "mimetype")
                    self.assertEqual(archive.infolist()[0].compress_type, zipfile.ZIP_STORED)

    def test_xml_preserves_doctype_comments_processing_instructions_and_paths(self):
        root = parse_xml(xhtml("<p>Hello <b>world</b>!</p>").encode(), "book.xhtml")
        child = root.find(".//{http://www.w3.org/1999/xhtml}b")
        self.assertIs(resolve_element(root, element_path(root, child)), child)
        serialized = serialize_xml(root)
        self.assertIn(b"<!DOCTYPE html>", serialized)
        self.assertIn(b"<?outside preserved?>", serialized)
        self.assertIn(b"<!--keep-->", serialized)
        self.assertEqual(
            canonical_document(root), canonical_document(parse_xml(serialized, "book.xhtml"))
        )

    def test_unsafe_members_and_duplicates_fail(self):
        book = self.book()
        for index, name in enumerate(
            ("../escape", "/absolute", "a/../b", "a\\b", "a//b", book.documents[0])
        ):
            path = self.directory / f"unsafe-{index}.epub"
            with zipfile.ZipFile(path, "w") as archive:
                for resource, data in book.entries.items():
                    archive.writestr(resource, data, compress_type=zipfile.ZIP_STORED)
                archive.writestr(name, b"bad")
            with self.subTest(name=name), self.assertRaises(ShuyiError):
                load_book(path)

    def test_symlink_and_member_limit_fail(self):
        book = self.book()
        path = self.directory / "symlink.epub"
        with zipfile.ZipFile(path, "w") as archive:
            for resource, data in book.entries.items():
                archive.writestr(resource, data)
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            archive.writestr(info, b"book.xhtml")
        with self.assertRaises(ShuyiError):
            load_book(path)
        with patch("shuyi.epub.archive.MAX_MEMBERS", 1), self.assertRaises(ShuyiError):
            load_book(book.source_path)
        with patch("shuyi.epub.archive.MAX_MEMBER", 8), self.assertRaises(ShuyiError):
            load_book(book.source_path)

    def test_broken_fragment_and_traversal_reference_fail(self):
        for index, href in enumerate(("#missing", "../../escape", "missing.xhtml")):
            path = build_epub(
                self.directory / f"reference-{index}.epub",
                documents={"book.xhtml": xhtml(f'<p><a href="{href}">Link</a></p>')},
            )
            with self.subTest(href=href), self.assertRaises(ShuyiError):
                load_book(path)
        self.assertEqual(
            resolve_href("OPS/text/book.xhtml", "../images/a%20b.svg#shape"),
            ("OPS/images/a b.svg", "shape"),
        )
        self.assertIsNone(resolve_href("book.xhtml", "https://example.com/a"))

    def test_invalid_package_and_signature_fail(self):
        book = self.book()
        malformed = book.entries[book.opf_path].replace(b'version="3.0"', b'version="4.0"')
        if malformed == book.entries[book.opf_path]:
            malformed = b"<package/>"
        with self.assertRaises(ShuyiError):
            load_book(self.repack(book, {book.opf_path: malformed}))
        with self.assertRaises(ShuyiError):
            load_book(
                self.repack(book, {"META-INF/signatures.xml": b"<signatures/>"}, "signed.epub")
            )

    def test_entities_fail_without_resolving_external_resources(self):
        data = (
            b'<!DOCTYPE root [<!ENTITY secret SYSTEM "file:///etc/passwd">]><root>&secret;</root>'
        )
        with self.assertRaises(ShuyiError):
            parse_xml(data, "book.xhtml")

    def test_protection_covers_ancestors_and_descendants(self):
        root = parse_xml(
            xhtml("<section><p>Text <code><b>Code</b></code></p></section>").encode(), "book.xhtml"
        )
        for name in ("section", "p", "code", "b"):
            self.assertTrue(is_protected(root.find(f".//{{http://www.w3.org/1999/xhtml}}{name}")))

    def test_notes_require_explicit_or_safe_reciprocal_semantics(self):
        book = self.book(
            '<p><a id="ref" href="#note" epub:type="noteref"><sup>1</sup></a> tail.</p>'
            '<aside id="note" epub:type="footnote"><a href="#ref">↩</a> Note.</aside>'
        )
        markers = find_note_markers(book)
        self.assertEqual([marker.kind for marker in markers], ["noteref", "backlink"])
        resource = book.documents[0]
        root = parse_xml(book.entries[resource], resource)
        anchor = resolve_element(root, markers[0].path)
        self.assertEqual(
            [(field, getattr(node, field)) for node, field in label_fields(anchor)], [("text", "1")]
        )
        self.assertEqual(anchor.tail, " tail.")
        arbitrary = self.directory / "arbitrary.epub"
        build_epub(
            arbitrary,
            documents={
                "book.xhtml": xhtml(
                    '<p><a id="a" href="#b">1</a></p><p id="b"><a href="#a">1</a> Ordinary.</p>'
                )
            },
        )
        self.assertEqual(find_note_markers(load_book(arbitrary)), ())

    def test_note_symbol_pair_and_legacy_anchor_names(self):
        book = self.book(
            '<p><a name="ref" href="#note">*</a></p>'
            '<p id="note"><a href="#ref">↩</a> Actual note.</p>'
        )
        self.assertEqual(len(find_note_markers(book)), 2)

    def test_crc_corruption_fails(self):
        book = self.book()
        source = bytearray(book.source_path.read_bytes())
        info = book.infos["mimetype"]
        start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        source[start] ^= 1
        path = self.directory / "crc.epub"
        path.write_bytes(source)
        with self.assertRaises(ShuyiError):
            load_book(path)

    def test_write_refuses_existing_destination(self):
        book = self.book()
        path = self.directory / "occupied.epub"
        path.write_bytes(b"keep")
        with self.assertRaises(ShuyiError):
            write_book(book, {}, path)
        self.assertEqual(path.read_bytes(), b"keep")

    def test_document_inventory_uses_media_type_not_filename(self):
        path = build_epub(
            self.directory / "media.epub",
            documents={"chapter.data": xhtml("<p>Declared XHTML.</p>")},
            extra_entries={"misleading.xhtml": b"opaque bytes"},
        )
        book = load_book(path)
        self.assertEqual(book.documents, ("chapter.data",))
        self.assertEqual(book.entries["misleading.xhtml"], b"opaque bytes")

    def test_cover_nav_and_fixed_layout_are_protected(self):
        from lxml import etree

        from shuyi.epub.dom import OPF

        path = build_epub(
            self.directory / "protected.epub",
            documents={
                "book.xhtml": xhtml("<p>Body.</p>"),
                "cover.xhtml": xhtml("<p>Cover.</p>"),
                "nav.xhtml": xhtml('<nav><a href="book.xhtml">Book</a></nav>'),
            },
            protected=("cover.xhtml",),
        )
        book = load_book(path)
        root = parse_xml(book.entries[book.opf_path], book.opf_path)
        for item in root.findall(f"{{{OPF}}}manifest/{{{OPF}}}item"):
            if item.get("href") == "nav.xhtml":
                item.set("properties", "nav")
        root.find(f"{{{OPF}}}spine/{{{OPF}}}itemref").set(
            "properties", "rendition:layout-pre-paginated"
        )
        output = load_book(self.repack(book, {book.opf_path: serialize_xml(root)}))
        self.assertEqual(output.protected, frozenset({"book.xhtml", "cover.xhtml", "nav.xhtml"}))
        metadata = root.find(f"{{{OPF}}}metadata")
        etree.SubElement(
            metadata, f"{{{OPF}}}meta", property="rendition:layout"
        ).text = "pre-paginated"
        output = load_book(
            self.repack(book, {book.opf_path: serialize_xml(root)}, "all-fixed.epub")
        )
        self.assertTrue(set(output.documents) <= output.protected)

    def test_drm_fails_but_declared_font_obfuscation_roundtrips(self):
        path = build_epub(self.directory / "fonts.epub", extra_entries={"font.ttf": b"font bytes"})
        book = load_book(path)

        def encryption(algorithm, target):
            return (
                f'<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                f'<EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#">'
                f'<EncryptionMethod Algorithm="{algorithm}"/>'
                f'<CipherData><CipherReference URI="{target}"/></CipherData>'
                f"</EncryptedData></encryption>"
            ).encode()

        font = encryption("http://www.idpf.org/2008/embedding", "font.ttf")
        output = load_book(self.repack(book, {"META-INF/encryption.xml": font}, "font-output.epub"))
        self.assertEqual(output.entries["font.ttf"], b"font bytes")
        for index, data in enumerate(
            (
                encryption("urn:drm:encrypted", "font.ttf"),
                encryption("http://www.idpf.org/2008/embedding", "book.xhtml"),
            )
        ):
            with self.subTest(index=index), self.assertRaises(ShuyiError):
                load_book(self.repack(book, {"META-INF/encryption.xml": data}, f"drm-{index}.epub"))

    def test_numeric_reciprocal_pair_needs_exact_package_note_context(self):
        book = self.book(
            '<p><a id="ref" href="#note">1</a></p><p id="note"><a href="#ref">↩</a> Note.</p>'
        )
        self.assertEqual(find_note_markers(book), ())
        from lxml import etree

        from shuyi.epub.dom import OPF

        root = parse_xml(book.entries[book.opf_path], book.opf_path)
        guide = root.find(f"{{{OPF}}}guide")
        etree.SubElement(guide, f"{{{OPF}}}reference", type="notes", href="book.xhtml#note")
        output = load_book(self.repack(book, {book.opf_path: serialize_xml(root)}))
        self.assertEqual(len(find_note_markers(output)), 2)
