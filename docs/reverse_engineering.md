# Reverse-engineering guide

Resolve stores many per-clip settings inside undocumented hexadecimal blobs
("FieldsBlobs"). This guide describes the workflow for mapping them.

## The differential method

The core tool is `drp diff`, which compares two nearly identical projects and
reports the exact bytes that changed:

1. Create a minimal project in Resolve: one timeline, one clip.
2. Export it: `before.drp`.
3. Change **exactly one** setting in Resolve (e.g. enable AI Super Scale on
   the clip).
4. Export again: `after.drp`.
5. Compare:

```
$ drp diff before.drp after.drp
Timeline: Timeline 1
  Clip: Camera001.mov
    UUID: 0301f9c2-...
    FieldsBlob
      Offset 0x0048
        Old: 01
        New: 00
```

6. Repeat with different values of the same setting to learn the encoding
   (byte width, endianness, enum values, float vs int).

Tips:

* Always toggle a *single* setting between exports; Resolve may also bump
  timestamps or counters — recurring offsets across many experiments are the
  ones that matter.
* `drp dump-fields project.drp --clip <name>` hex-dumps a clip's blob with
  any already-known fields decoded, which helps orient new discoveries.
* `drp export-json project.drp --fields -o dump.json` gives you the raw hex
  of every blob for scripting your own analysis.

## Recording discoveries: signature databases

Once a field is mapped, record it in a JSON signature database:

```json
{
  "clip": [
    {
      "name": "super_scale",
      "offset": 72,
      "type": "uint8",
      "description": "AI Super Scale mode; 0 = disabled, observed in Resolve 19.x"
    },
    {
      "name": "retime_factor",
      "offset": 96,
      "type": "double",
      "description": "playback speed multiplier"
    }
  ]
}
```

Supported types: `uint8/16/32/64`, `int8/16/32/64`, `float`, `double`, and
`bytes` (requires explicit `"size"`). Multi-byte fields default to
little-endian; set `"endianness": "big"` if needed.

Load it globally for any CLI command:

```bash
drp --signatures resolve19.json dump-fields project.drp
```

or in code:

```python
from drp_editor import default_registry
default_registry.load_json("resolve19.json")
```

**Record the Resolve version in the file name and descriptions** — offsets
are not guaranteed stable across versions.

## Safety model

You can never corrupt unknown data by accident:

* Blob edits go through `FieldsBlob.set_field`, which patches only the bytes
  covered by the field's spec.
* Unknown bytes, unknown XML nodes, and non-XML archive members round-trip
  byte-for-byte.
* Every change produces a `Patch` record; use `--log patches.json` with
  `drp patch` to keep an audit trail.

## Known limitations / open questions

* The byte diff is positional; if Resolve *inserts* bytes into a blob
  (variable-length layouts), the diff after the insertion point becomes
  noisy. Compare blob lengths first — a length change is the tell.
* Blob layouts are assumed fixed-offset. Variable layouts will need a schema
  upgrade in `fields_blob.py` (e.g. tag-length-value walkers built on
  `BinaryReader`).
* Which archive members besides the project XML matter (render cache
  indexes, stills, ...) is currently unmapped; the archive layer preserves
  all of them untouched.
