# .drp file format notes

Working notes on the DaVinci Resolve project export format. Everything here
is based on observation, not documentation; treat it as hypotheses with
confidence levels.

## Container

* A `.drp` export is a **ZIP archive** (confirmed by magic bytes `PK\x03\x04`).
* Some tooling passes around **bare project XML** with a `.drp` extension;
  `DRPArchive` detects this (leading `<`) and wraps it as a synthetic
  single-member archive named `project.xml`.
* Member inventory varies by project; the project database dump is the
  largest XML member. `DRPParser._select_xml_member` prefers well-known
  names (`project.xml`) and falls back to the largest XML member.

## Project XML

* The XML is a dump of Resolve's internal project database. Tag names vary
  across versions; the parser therefore matches tags by pattern
  (`ParserConfig`) instead of exact names.
* Object identity is UUID-based. UUIDs appear as attributes or child
  elements under several key spellings (`Uuid`, `UUID`, `DbId`, ...).
* Clip <-> media pool references are by media item UUID.

## FieldsBlobs

* Many settings are serialized into hex strings stored in attributes or
  child elements commonly named `Fields` / `FieldsBlob`.
* Observed encodings in mapped fields so far: little-endian integers and
  IEEE-754 floats at fixed offsets (see the signature databases under
  `examples/`).
* Blob length can differ between clips of different types — decoders must
  bounds-check every field (ours skip fields that do not fit).

## Unknowns

* Exact semantics of most blob bytes.
* Whether blobs are versioned internally (a leading version tag would
  explain cross-version offset shifts).
* Purpose of auxiliary binary archive members.

When you confirm or refute any of the above, update this file and the
signature databases together.
