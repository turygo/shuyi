from __future__ import annotations

import copy
import hashlib
import stat
import zipfile
import zlib
from pathlib import Path

from shuyi.contracts import Book, ShuyiError
from shuyi.epub.dom import parse_xml
from shuyi.epub.package import check_references, package_inventory, resolve_href

MAX_MEMBERS = 10_000
MAX_MEMBER = 128 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
FONT_ALGORITHMS = {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}


def safe_member_name(name: str) -> str:
    parts = name.rstrip("/").split("/")
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or "\x00" in name
        or any(part in {"", ".", ".."} for part in parts)
        or ":" in parts[0]
        or any(ord(char) < 32 for char in name)
    ):
        raise ShuyiError("archive_path_unsafe")
    return "/".join(parts)


def read_entries(path: Path) -> tuple[dict[str, bytes], dict[str, zipfile.ZipInfo]]:
    entries: dict[str, bytes] = {}
    infos: dict[str, zipfile.ZipInfo] = {}
    aliases: set[str] = set()
    total = 0
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_MEMBERS:
                raise ShuyiError("archive_limit")
            for info in members:
                if info.orig_filename != info.filename:
                    raise ShuyiError("archive_path_unsafe")
                alias = safe_member_name(info.filename)
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if alias in aliases:
                    raise ShuyiError("archive_duplicate")
                aliases.add(alias)
                if info.flag_bits & 1:
                    raise ShuyiError("archive_encrypted")
                if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or (
                    kind == stat.S_IFDIR and not info.is_dir()
                ):
                    raise ShuyiError("archive_special_entry")
                total += info.file_size
                if info.file_size > MAX_MEMBER or total > MAX_TOTAL:
                    raise ShuyiError("archive_limit")
                with archive.open(info) as member:
                    data = member.read(MAX_MEMBER + 1)
                    if len(data) != info.file_size or len(data) > MAX_MEMBER:
                        raise ShuyiError("archive_limit")
                entries[info.filename] = data
                infos[info.filename] = info
            if not members or members[0].filename != "mimetype":
                raise ShuyiError("mimetype_invalid")
            if (
                entries.get("mimetype") != b"application/epub+zip"
                or infos["mimetype"].compress_type != zipfile.ZIP_STORED
            ):
                raise ShuyiError("mimetype_invalid")
    except (
        OSError,
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        EOFError,
        zlib.error,
    ) as exc:
        raise ShuyiError("archive_invalid") from exc
    return entries, infos


def check_encryption(entries: dict[str, bytes], fonts: set[str] | None = None) -> None:
    if "META-INF/signatures.xml" in entries:
        raise ShuyiError("archive_signed")
    if "META-INF/encryption.xml" not in entries:
        return
    resource = "META-INF/encryption.xml"
    root = parse_xml(entries[resource], resource)
    encrypted = root.findall(".//{http://www.w3.org/2001/04/xmlenc#}EncryptedData")
    if not encrypted:
        raise ShuyiError("encryption_invalid", resource)
    for node in encrypted:
        method = node.find("{http://www.w3.org/2001/04/xmlenc#}EncryptionMethod")
        if method is None or method.get("Algorithm") not in FONT_ALGORITHMS:
            raise ShuyiError("drm_unsupported", resource)
        reference = node.find(".//{http://www.w3.org/2001/04/xmlenc#}CipherReference")
        target = (
            resolve_href("container.xml", reference.get("URI", ""))
            if reference is not None
            else None
        )
        if target is None or target[1] or target[0] not in entries:
            raise ShuyiError("encryption_invalid", resource)
        if fonts is not None and target[0] not in fonts:
            raise ShuyiError("drm_unsupported", resource)


def load_book(path: Path) -> Book:
    path = Path(path)
    entries, infos = read_entries(path)
    check_encryption(entries)
    opf, documents, protected, version = package_inventory(entries)
    try:
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
    except OSError as exc:
        raise ShuyiError("archive_invalid") from exc
    book = Book(path, digest, entries, infos, opf, documents, protected, version)
    from shuyi.epub.package import manifest_resources

    fonts = {
        resource
        for resource, media in manifest_resources(book).items()
        if media.startswith("font/")
        or media
        in {
            "application/font-sfnt",
            "application/font-woff",
            "application/vnd.ms-opentype",
            "application/x-font-truetype",
            "application/x-font-opentype",
        }
    }
    check_encryption(entries, fonts)
    check_references(book)
    return book


def write_book(book: Book, replacements: dict[str, bytes], destination: Path) -> None:
    names = ["mimetype"] + [name for name in book.entries if name != "mimetype"]
    names += [name for name in replacements if name not in book.entries]
    aliases = [safe_member_name(name) for name in names]
    if len(aliases) != len(set(aliases)):
        raise ShuyiError("archive_duplicate")
    sizes = [len(replacements.get(name, book.entries.get(name, b""))) for name in names]
    if (
        len(names) > MAX_MEMBERS
        or any(size > MAX_MEMBER for size in sizes)
        or sum(sizes) > MAX_TOTAL
    ):
        raise ShuyiError("archive_limit")
    try:
        with zipfile.ZipFile(destination, "x") as archive:
            for name in names:
                data = replacements.get(name, book.entries.get(name, b""))
                info = copy.copy(book.infos[name]) if name in book.infos else zipfile.ZipInfo(name)
                if name == "mimetype":
                    data = b"application/epub+zip"
                    info.compress_type = zipfile.ZIP_STORED
                elif name not in book.infos:
                    info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise ShuyiError("archive_write_failed") from exc
