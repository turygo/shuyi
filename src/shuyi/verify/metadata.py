from __future__ import annotations

import re
from datetime import datetime

from lxml import etree

from shuyi.contracts import Book, ShuyiError, ThemePlan
from shuyi.epub.dom import OPF, element_path


def reverse_modified(
    book: Book, plan: ThemePlan, original: etree._Element, changed: etree._Element
) -> None:
    old = original.findall(f"{{{OPF}}}metadata/{{{OPF}}}meta[@property='dcterms:modified']")
    new = changed.findall(f"{{{OPF}}}metadata/{{{OPF}}}meta[@property='dcterms:modified']")
    edits = [edit for edit in plan.texts if edit.resource == book.opf_path]
    if book.version != "3.0":
        if edits:
            raise ShuyiError("verification_metadata_forbidden", book.opf_path)
        return
    if len(old) > 1 or len(new) != 1:
        raise ShuyiError("verification_metadata_invalid", book.opf_path)
    value = new[0].text or ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise ShuyiError("verification_metadata_invalid", book.opf_path)
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ShuyiError("verification_metadata_invalid", book.opf_path) from exc
    if old:
        if value == old[0].text:
            if edits:
                raise ShuyiError("verification_metadata_invalid", book.opf_path)
            return
        if (
            len(edits) != 1
            or edits[0].path != element_path(original, old[0])
            or edits[0].field != "text"
            or edits[0].before != old[0].text
            or edits[0].after != value
        ):
            raise ShuyiError("verification_metadata_invalid", book.opf_path)
        new[0].text = old[0].text
    else:
        if (
            edits
            or dict(new[0].attrib) != {"property": "dcterms:modified"}
            or len(new[0])
            or new[0].tail
        ):
            raise ShuyiError("verification_metadata_invalid", book.opf_path)
        new[0].getparent().remove(new[0])
