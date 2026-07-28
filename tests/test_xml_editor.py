"""Tests for format-preserving XML editing."""

from __future__ import annotations

import pytest

from drp_editor.exceptions import XMLParseError
from drp_editor.xml_editor import XMLDocument

DOC = b"""<?xml version="1.0" encoding="UTF-8"?>
<Root attr="1">
  <!-- important comment -->
  <Child keep="yes">text</Child>
  <Unknown mystery="?"/>
</Root>
"""


class TestPreservation:
    def test_unmodified_document_is_byte_identical(self):
        doc = XMLDocument(DOC)
        assert doc.to_bytes() == DOC

    def test_edit_preserves_comments_and_unknown_nodes(self):
        doc = XMLDocument(DOC)
        child = doc.xpath("//Child")[0]
        doc.set_attribute(child, "keep", "no")
        out = doc.to_bytes()
        assert b"<!-- important comment -->" in out
        assert b'<Unknown mystery="?"/>' in out
        assert b'keep="no"' in out
        assert out.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
        assert out.endswith(b"\n")

    def test_setting_same_value_keeps_document_clean(self):
        doc = XMLDocument(DOC)
        child = doc.xpath("//Child")[0]
        doc.set_attribute(child, "keep", "yes")
        assert not doc.dirty
        assert doc.to_bytes() == DOC

    def test_set_text(self):
        doc = XMLDocument(DOC)
        child = doc.xpath("//Child")[0]
        old = doc.set_text(child, "new text")
        assert old == "text"
        assert b'<Child keep="yes">new text</Child>' in doc.to_bytes()

    def test_invalid_xml_raises(self):
        with pytest.raises(XMLParseError):
            XMLDocument(b"<Root><unclosed>")

    def test_no_trailing_newline_preserved(self):
        doc_bytes = DOC.rstrip(b"\n")
        doc = XMLDocument(doc_bytes)
        child = doc.xpath("//Child")[0]
        doc.set_attribute(child, "keep", "no")
        assert not doc.to_bytes().endswith(b"\n")
