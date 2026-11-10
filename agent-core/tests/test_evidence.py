"""T7 证据对象与 EvidenceStore：内容寻址不可覆盖、血缘可回溯。"""

from pathlib import Path

import pytest
from agent_core.evidence import Asset, Evidence, Observation, SqliteEvidenceStore


@pytest.fixture
def store(tmp_path: Path) -> SqliteEvidenceStore:
    return SqliteEvidenceStore(db_path=tmp_path / "ev.db", blob_dir=tmp_path / "blobs")


async def test_write_asset_content_addressing(store: SqliteEvidenceStore, tmp_path: Path) -> None:
    data = b"original-pdf-bytes"
    a1 = await store.write_asset(data, kind="document", source="upload")
    a2 = await store.write_asset(data, kind="document", source="retry")
    # 同内容 → 同资产（sha 决定身份），blob 只有一份
    assert a1.id == a2.id
    assert a1.sha256 == a2.sha256
    blobs = list((tmp_path / "blobs").iterdir())
    assert len(blobs) == 1
    assert blobs[0].read_bytes() == data


async def test_blob_immutable(store: SqliteEvidenceStore, tmp_path: Path) -> None:
    data = b"v1"
    a1 = await store.write_asset(data, kind="document")
    blob = tmp_path / "blobs" / a1.sha256
    before = blob.read_bytes()
    # 再次写入同内容（即使来源不同）不得改写既有 blob
    await store.write_asset(data, kind="document", source="another")
    assert blob.read_bytes() == before
    # 派生结果（不同内容）是另一个资产，不覆盖原始
    derived = await store.write_asset(b"v2-derived", kind="structured", source=f"derived:{a1.id}")
    assert derived.sha256 != a1.sha256
    assert blob.read_bytes() == before


async def test_observation_roundtrip(store: SqliteEvidenceStore) -> None:
    a = await store.write_asset(b"img", kind="image")
    obs = Observation(
        environment_version="workbench-1",
        asset_refs=[a.id],
        state_summary={"page": 3, "visible_fields": 12},
    )
    obs_id = await store.write_observation(obs)
    assert obs_id == obs.id


async def test_lineage_links_evidence_to_observations(store: SqliteEvidenceStore) -> None:
    a1 = await store.write_asset(b"doc-1", kind="document")
    a2 = await store.write_asset(b"scan-1", kind="image")
    o1 = await store.write_observation(
        Observation(environment_version="w1", asset_refs=[a1.id], state_summary={"ok": True})
    )
    o2 = await store.write_observation(
        Observation(environment_version="w1", asset_refs=[a2.id], state_summary={"ok": True})
    )
    ev_id = await store.write_evidence(
        claim={"statement": "导出文件包含全部 5 页", "checks": ["page_count"]},
        observation_refs=[o1, o2],
        derivation={"process": "page_count == 5 from parser"},
    )
    lineage = await store.trace_lineage(ev_id)
    assert lineage["evidence"]["claim"]["statement"].startswith("导出文件")
    assert lineage["evidence"]["verification_status"] == "unverified"
    assert {obs["id"] for obs in lineage["observations"]} == {o1, o2}
    assets = {ref for obs in lineage["observations"] for ref in obs["asset_refs"]}
    assert assets == {a1.id, a2.id}


async def test_trace_missing_evidence_raises(store: SqliteEvidenceStore) -> None:
    with pytest.raises(KeyError):
        await store.trace_lineage("ev_nope")


def test_models_are_pydantic() -> None:
    ev = Evidence(claim={"s": 1}, observation_refs=["o1"], derivation={})
    assert Evidence.model_validate_json(ev.model_dump_json()) == ev
    seg = Asset(id="a", kind="image", storage_ref="x", sha256="h", source="t")
    assert seg.kind == "image"


async def test_trace_lineage_empty_refs(store: SqliteEvidenceStore) -> None:
    """M3: 空 observation_refs 的证据 → 空 lineage，不炸 SQL。"""
    ev_id = await store.write_evidence(claim={"s": 1}, observation_refs=[], derivation={})
    lineage = await store.trace_lineage(ev_id)
    assert lineage["evidence"]["claim"] == {"s": 1}
    assert lineage["observations"] == []
