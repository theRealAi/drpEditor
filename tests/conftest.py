"""Shared fixtures: synthetic .drp archives with known structure.

Real Resolve exports cannot be committed (size, licensing), so the suite
builds small archives that exercise the same code paths: ZIP container,
project XML with timelines/clips/media/settings, hex FieldsBlobs, and an
opaque binary member that must survive round-trips untouched.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from drp_editor.fields_blob import BlobSchemaRegistry, FieldSpec, default_registry

#: Offset of the synthetic "super_scale" byte inside test blobs.
SUPER_SCALE_OFFSET = 0x48

BLOB_SIZE = 0x50


def make_blob(super_scale: int) -> bytes:
    """80-byte blob: recognizable ramp with a super-scale byte at 0x48."""
    data = bytearray(range(BLOB_SIZE))
    data[SUPER_SCALE_OFFSET] = super_scale
    return bytes(data)


BLOB_ENABLED = make_blob(1)
BLOB_DISABLED = make_blob(0)

SAMPLE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<Project Name="Demo Project">
  <!-- exported by the drp_editor test suite -->
  <Settings FrameRate="24" Width="1920" Height="1080"/>
  <MediaPool>
    <SyMediaPoolItem Uuid="m-001" Name="Camera001.mov" FilePath="/media/Camera001.mov"/>
    <SyMediaPoolItem Uuid="m-002" Name="Camera002.mov" FilePath="/media/Camera002.mov"/>
  </MediaPool>
  <TimelineList>
    <SyTimeline Uuid="t-001" Name="Main Timeline">
      <Track index="1">
        <SyClip Uuid="c-001" Name="Camera001.mov" Source="m-001" Fields="{BLOB_ENABLED.hex()}"/>
        <SyClip Uuid="c-002" Name="Camera002.mov" Source="m-002" Fields="{BLOB_DISABLED.hex()}"/>
      </Track>
    </SyTimeline>
    <SyTimeline Uuid="t-002" Name="Second Timeline">
      <Track index="1">
        <SyClip Uuid="c-003" Source="m-001">
          <Name>NestedNameClip</Name>
          <Fields>{BLOB_ENABLED.hex()}</Fields>
        </SyClip>
      </Track>
    </SyTimeline>
  </TimelineList>
  <UnknownFutureBlock keep="me">
    <Nested attr="untouched"/>
  </UnknownFutureBlock>
</Project>
""".encode()

#: Opaque non-XML payload that must round-trip byte-for-byte.
BINARY_MEMBER = bytes(range(256)) * 4


def build_drp(path: Path, xml: bytes = SAMPLE_XML) -> Path:
    """Write a synthetic .drp ZIP archive to *path*."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.xml", xml)
        zf.writestr("render/thumbnail.bin", BINARY_MEMBER)
    return path


@pytest.fixture()
def drp_file(tmp_path: Path) -> Path:
    """A fresh synthetic .drp on disk."""
    return build_drp(tmp_path / "sample.drp")


# -- Resolve-layout archive (mimics real exports) ---------------------------

#: SeqContainer uuid used by the resolve-like fixture.
SEQ_UUID = "aaaa1111-2222-3333-4444-555566667777"


def _utf16be_hex(value: str) -> str:
    return value.encode("utf-16-be").hex()


RESOLVE_PROJECT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<!--DbAppVer="21.0.0b.0020" DbPrjVer="17"-->
<SM_Project DbId="p-0001">
 <FieldsBlob/>
 <ProjectName>RealStyle</ProjectName>
 <PowerNodeList>
  <ListMgt::LmPowerNodeList DbId="pn-01">
   <FieldsBlob/>
  </ListMgt::LmPowerNodeList>
 </PowerNodeList>
</SM_Project>
"""

RESOLVE_MPFOLDER_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<Sm2MpFolder DbId="fold-01">
 <Name>Master</Name>
 <MediaVec>
  <Element>
   <Sm2MpVideoClip DbId="pool-vid-1">
    <FieldsBlob/>
    <Name>Camera001.mov</Name>
    <Video>
     <BtVideoInfo DbId="bt-01">
      <Clip>00aa11bb</Clip>
     </BtVideoInfo>
    </Video>
   </Sm2MpVideoClip>
  </Element>
  <Element>
   <Sm2MpTimelineClip DbId="pool-tl-1">
    <FieldsBlob/>
    <Name>Main Timeline</Name>
    <TimelineSharedHandle>
     <Sm2Timeline DbId="handle-01">
      <FieldsBlob/>
      <Name>Main Timeline</Name>
      <Sequence>
       <Sm2Sequence DbId="seq-01">
        <FieldsBlob>{_utf16be_hex(SEQ_UUID)}</FieldsBlob>
       </Sm2Sequence>
      </Sequence>
     </Sm2Timeline>
    </TimelineSharedHandle>
   </Sm2MpTimelineClip>
  </Element>
 </MediaVec>
</Sm2MpFolder>
""".encode()

RESOLVE_SEQ_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<Sm2SequenceContainer DbId="{SEQ_UUID}">
 <FieldsBlob/>
 <VideoTrackVec>
  <Element>
   <Sm2TiTrack DbId="track-01">
    <Items>
     <Element>
      <Sm2TiVideoClip DbId="ticlip-01">
       <FieldsBlob>{BLOB_ENABLED.hex()}</FieldsBlob>
       <Name>Camera001.mov</Name>
       <MediaRef>pool-vid-1</MediaRef>
      </Sm2TiVideoClip>
     </Element>
     <Element>
      <Sm2TiVideoClip DbId="ticlip-02">
       <FieldsBlob>{BLOB_DISABLED.hex()}</FieldsBlob>
       <Name>Camera001.mov</Name>
       <MediaRef>pool-vid-1</MediaRef>
      </Sm2TiVideoClip>
     </Element>
    </Items>
   </Sm2TiTrack>
  </Element>
 </VideoTrackVec>
</Sm2SequenceContainer>
""".encode()


def build_resolve_like_drp(path: Path) -> Path:
    """A .drp mimicking real Resolve layout: multi-file, C++ tag names."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.xml", RESOLVE_PROJECT_XML)
        zf.writestr("MediaPool/Master/MpFolder.xml", RESOLVE_MPFOLDER_XML)
        zf.writestr(f"SeqContainer/{SEQ_UUID}.xml", RESOLVE_SEQ_XML)
    return path


@pytest.fixture()
def resolve_drp(tmp_path: Path) -> Path:
    """A fresh resolve-layout .drp on disk."""
    return build_resolve_like_drp(tmp_path / "realstyle.drp")


@pytest.fixture()
def clip_registry() -> BlobSchemaRegistry:
    """A registry that knows the synthetic super_scale field."""
    registry = BlobSchemaRegistry()
    registry.schema("clip").add(
        FieldSpec(
            name="super_scale",
            offset=SUPER_SCALE_OFFSET,
            type="uint8",
            description="synthetic AI Super Scale mode for tests",
        )
    )
    return registry


@pytest.fixture()
def isolated_default_registry():
    """Snapshot/restore the process-wide registry around a test."""
    saved = dict(default_registry._schemas)
    default_registry._schemas.clear()
    yield default_registry
    default_registry._schemas.clear()
    default_registry._schemas.update(saved)
