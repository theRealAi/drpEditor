# Architecture

## Layering

```
CLI (cli.py)
  └── Repairs (repairs/*)          plugin framework: scan / repair / validate
        └── Model (models.py)      Project/Timeline/Clip/... + patch-recording mutation
              ├── Parser (parser.py)          XML -> model, configurable heuristics
              ├── Patch  (patch.py)           audit trail, undo
              ├── Diff   (diff.py)            model + binary diffing
              ├── Validation (validation.py)  read-only checks
              └── FieldsBlob (fields_blob.py) hex blobs, schemas, signatures
                    └── Binary (binary.py)    BinaryReader / BinaryWriter
  XML editor (xml_editor.py)       format-preserving lxml wrapper
  Archive (archive.py)             .drp container, byte-preserving rebuild
```

Each layer only depends on the layers below it. The archive layer knows
nothing about XML; the XML layer knows nothing about Resolve semantics;
the model layer never touches bytes directly.

## Data-preservation strategy

Preservation is enforced at three levels, each with a *dirty* flag:

1. **Archive** (`DRPArchive`): if no member was replaced or added, `save()`
   writes the original file bytes verbatim. Otherwise the ZIP is rebuilt
   keeping member order, compression type, timestamps, and attributes;
   untouched members keep their exact decompressed content.
2. **XML** (`XMLDocument`): `to_bytes()` returns the original serialized
   bytes until the first real mutation. Setting an attribute to its current
   value does not mark the document dirty.
3. **Blob** (`FieldsBlob`): `to_hex()` returns the original hex text until
   a field is actually changed; edits patch only the field's own bytes and
   preserve the original hex case on re-encode.

The result: `open_project(x).save(y)` produces `y == x` byte-for-byte.

## Mutation flow

All edits go through `Project`:

```
project.set_property(clip, "name", "New")   # or set_blob_field(...)
  ├── PropertyCarrier.write(...)   edits exactly one attribute / text node
  ├── model field updated, caches rebuilt if needed
  └── PatchLog.record(...)         auditable, undoable via project.undo_last()
```

A `PropertyCarrier` is recorded by the parser for every extracted property
and pins down *where* the value physically lives (attribute vs. child
element text), so writes cannot touch anything else.

## Parser configurability

`ParserConfig` holds case-insensitive tag regexes (`timeline$`, `clip$`,
`media\w*item`, ...) and property-key conventions (`Uuid`/`DbId`/...,
`Name`/`ClipName`/..., `Fields`/`FieldsBlob`/...). Resolve schema drift is
handled by adding configs, not by changing parser logic. The parser makes a
single pass over the tree and builds UUID/name caches, keeping 100k-clip
projects fast.

## Performance

* One traversal of the XML tree at parse time; O(1) cached lookups after.
* FieldsBlobs are decoded lazily on first access (`_BlobHolder`).
* Archive members are read lazily and cached.
* `flush()` writes back only blobs that are actually dirty.

## Extension points

* **New repair**: subclass `repairs.base.Repair`, decorate with
  `@register`, implement `scan()` / `repair()`; `validate()` has a sane
  default. Import it from `repairs/__init__.py` (or your own package).
* **New blob fields**: add entries to a JSON signature database and load it
  with `--signatures` or `default_registry.load_json(...)`.
* **New schema flavor**: instantiate `ParserConfig(...)` and pass it to
  `open_project(path, config=...)`.

## Error handling

Everything raised intentionally derives from `drp_editor.DRPError`:

```
DRPError
 ├── ArchiveError        container problems (bad zip, missing member)
 ├── XMLParseError       unparseable XML
 ├── ValidationError     hard validation failures
 ├── BinaryDecodeError   blob/binary decoding and encoding problems
 ├── PatchError          patch creation/application/undo problems
 ├── SaveError           write failures
 └── RepairError         repair plugin failures
```
