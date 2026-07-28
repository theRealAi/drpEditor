"""Format-preserving XML editing built on lxml.

The cardinal rule of this module: **never regenerate XML that was not
edited**. :class:`XMLDocument` keeps the original serialized bytes and
returns them verbatim until the first mutation. After a mutation, lxml
re-serializes the tree -- lxml preserves comments, element order,
whitespace between elements, and unknown nodes/attributes, so the output
stays as close to the source as possible.

Resolve quirk: C++-style tag names
----------------------------------
Resolve serializes C++ class names straight into tags, e.g.
``<ListMgt::LmPowerNodeList>``. That is not namespace-well-formed XML and
strict lxml refuses it. When such a document is encountered, tag names are
*sanitized* before parsing (colons replaced with a collision-checked
sentinel) and restored exactly on serialization. Unmodified documents
still return their original bytes verbatim, so the transformation is
invisible unless you inspect tag names of those specific elements.

Known limitations (only relevant once a document *is* edited):

* Attribute quoting is normalized to double quotes.
* The exact byte layout of the XML declaration may be normalized.

Both are accepted trade-offs; Resolve's own parser does not care, and
unmodified documents are still emitted byte-for-byte identical.
"""

from __future__ import annotations

import logging
import re

from lxml import etree

from .exceptions import XMLParseError

__all__ = ["XMLDocument"]

logger = logging.getLogger(__name__)

_DECL_RE = re.compile(rb"\A\s*(<\?xml[^>]*\?>)", re.S)

#: Matches an element name right after '<' or '</' (not comments/PIs/CDATA).
_TAG_NAME_RE = re.compile(rb"(</?)([A-Za-z_][^\s<>/=]*)")

#: lxml error fragments that indicate namespace-invalid names.
_QNAME_ERROR_MARKERS = ("Failed to parse QName", "Namespace prefix", "error parsing QName")


def _pick_sentinel(data: bytes) -> bytes:
    """A name-safe byte string guaranteed absent from *data*."""
    sentinel = b"__cln__"
    counter = 0
    while sentinel in data:
        counter += 1
        sentinel = b"__cln%d__" % counter
    return sentinel


def _sanitize_tag_names(data: bytes) -> tuple[bytes, bytes]:
    """Replace ':' inside tag names with a sentinel; return (data, sentinel)."""
    sentinel = _pick_sentinel(data)

    def fix(match: re.Match[bytes]) -> bytes:
        return match.group(1) + match.group(2).replace(b":", sentinel)

    return _TAG_NAME_RE.sub(fix, data), sentinel


class XMLDocument:
    """One XML file from the archive, edited in place with dirty tracking.

    Args:
        data: Original serialized bytes.
        source_name: Archive member name (for error messages only).
    """

    def __init__(self, data: bytes, *, source_name: str = "<memory>") -> None:
        self._original = data
        self._source_name = source_name
        self._dirty = False
        self._sentinel: bytes | None = None
        parser = etree.XMLParser(
            remove_blank_text=False,
            remove_comments=False,
            strip_cdata=False,
            resolve_entities=False,
            huge_tree=True,
        )
        try:
            self._tree: etree._ElementTree = etree.ElementTree(
                etree.fromstring(data, parser=parser)
            )
        except etree.XMLSyntaxError as exc:
            if not any(marker in str(exc) for marker in _QNAME_ERROR_MARKERS):
                raise XMLParseError(f"cannot parse XML in {source_name}: {exc}") from exc
            # Resolve writes C++ names like <ListMgt::LmPowerNodeList>;
            # sanitize tag names and retry (see module docstring).
            sanitized, sentinel = _sanitize_tag_names(data)
            try:
                self._tree = etree.ElementTree(etree.fromstring(sanitized, parser=parser))
            except etree.XMLSyntaxError as exc2:
                raise XMLParseError(f"cannot parse XML in {source_name}: {exc2}") from exc2
            self._sentinel = sentinel
            logger.info(
                "%s uses C++-style tag names; sanitized with sentinel %s",
                source_name,
                sentinel.decode(),
            )

    # -- accessors ------------------------------------------------------

    @property
    def root(self) -> etree._Element:
        """Root element of the document."""
        return self._tree.getroot()

    @property
    def tree(self) -> etree._ElementTree:
        """The underlying lxml element tree."""
        return self._tree

    @property
    def dirty(self) -> bool:
        """``True`` once any mutation has been recorded."""
        return self._dirty

    @property
    def source_name(self) -> str:
        """Archive member name this document was loaded from."""
        return self._source_name

    def mark_dirty(self) -> None:
        """Record that the tree was mutated (called by all edit helpers).

        Call this yourself if you mutate elements directly via lxml APIs.
        """
        if not self._dirty:
            logger.debug("XML document %s marked dirty", self._source_name)
        self._dirty = True

    # -- queries --------------------------------------------------------

    def xpath(self, expression: str) -> list[etree._Element]:
        """Run an XPath expression, returning matched elements only."""
        result = self.root.xpath(expression)
        if not isinstance(result, list):
            return []
        return [item for item in result if isinstance(item, etree._Element)]

    def iter(self, tag: str | None = None) -> etree.ElementDepthFirstIterator:
        """Depth-first iterator over the whole tree."""
        return self.root.iter(tag)

    def real_tag(self, element: etree._Element) -> str:
        """The element's original tag name, undoing any sanitization."""
        tag = element.tag if isinstance(element.tag, str) else ""
        if self._sentinel is not None:
            return tag.replace(self._sentinel.decode(), ":")
        return tag

    # -- mutations ------------------------------------------------------

    def set_attribute(self, element: etree._Element, name: str, value: str) -> str | None:
        """Set an attribute, returning the previous value (or ``None``)."""
        old = element.get(name)
        if old != value:
            element.set(name, value)
            self.mark_dirty()
        return old

    def set_text(self, element: etree._Element, value: str) -> str | None:
        """Set an element's text content, returning the previous text."""
        old = element.text
        if old != value:
            element.text = value
            self.mark_dirty()
        return old

    # -- serialization ---------------------------------------------------

    def to_bytes(self) -> bytes:
        """Serialize the document.

        Returns the original bytes verbatim if nothing was modified.
        Otherwise re-serializes via lxml, re-attaching the original XML
        declaration and trailing newline style.
        """
        if not self._dirty:
            return self._original
        # tostring returns bytes for byte encodings (str only for "unicode").
        raw = etree.tostring(self._tree, xml_declaration=False, encoding=self._encoding())
        body = raw if isinstance(raw, bytes) else raw.encode(self._encoding())
        if self._sentinel is not None:
            body = body.replace(self._sentinel, b":")
        match = _DECL_RE.match(self._original)
        prefix = match.group(1) + b"\n" if match else b""
        if self._original.endswith(b"\n") and not body.endswith(b"\n"):
            body += b"\n"
        elif not self._original.endswith(b"\n") and body.endswith(b"\n"):
            body = body.rstrip(b"\n")
        return prefix + body

    def _encoding(self) -> str:
        encoding = self._tree.docinfo.encoding
        return encoding if encoding else "UTF-8"
