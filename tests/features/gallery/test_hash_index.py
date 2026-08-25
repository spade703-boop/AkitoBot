"""Tests for the persistent gallery SHA-256 index."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import nonebot_plugin_akito.features.gallery as gallery
from nonebot_plugin_akito.features.gallery.hash_index import GalleryHashIndex, GalleryIndexSyncResult


def _make_index(tmp_path) -> GalleryHashIndex:
    return GalleryHashIndex(
        image_root=tmp_path,
        database_path=tmp_path / "gallery_hash_index.sqlite3",
        fixed_storage_keys=("toya", "groupmate"),
        image_suffixes={".jpg", ".jpeg", ".png", ".gif"},
    )


@pytest.mark.asyncio
async def test_restart_reuses_unchanged_hashes(monkeypatch, tmp_path):
    image_path = tmp_path / "toya" / "existing.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"existing-image")
    first_index = _make_index(tmp_path)

    first_result = await first_index.sync_incremental()

    assert first_result.rebuilt is True
    restarted_index = _make_index(tmp_path)

    def fail_hash(*args, **kwargs):
        pytest.fail("unchanged images should reuse the persisted hash")

    monkeypatch.setattr(restarted_index, "_hash_file_record", fail_hash)
    second_result = await restarted_index.sync_incremental()

    assert second_result.rebuilt is False
    assert second_result.updated_count == 0
    assert await restarted_index.is_duplicate(b"existing-image") is True


@pytest.mark.asyncio
async def test_incremental_sync_adds_updates_and_deletes_files(tmp_path):
    first_path = tmp_path / "toya" / "first.jpg"
    removed_path = tmp_path / "groupmate" / "removed.png"
    first_path.parent.mkdir(parents=True)
    removed_path.parent.mkdir(parents=True)
    first_path.write_bytes(b"first-version")
    removed_path.write_bytes(b"remove-me")
    hash_index = _make_index(tmp_path)
    await hash_index.sync_incremental()

    first_path.write_bytes(b"second-version-is-longer")
    removed_path.unlink()
    added_path = tmp_path / "custom" / "月城" / "added.gif"
    added_path.parent.mkdir(parents=True)
    added_path.write_bytes(b"added-image")

    result = await hash_index.sync_incremental()

    assert result.rebuilt is False
    assert result.updated_count == 2
    assert result.deleted_count == 1
    assert await hash_index.is_duplicate(b"first-version") is False
    assert await hash_index.is_duplicate(b"second-version-is-longer") is True
    assert await hash_index.is_duplicate(b"remove-me") is False
    assert await hash_index.is_duplicate(b"added-image") is True


@pytest.mark.asyncio
async def test_missing_database_is_rebuilt_without_touching_images(tmp_path):
    image_path = tmp_path / "toya" / "source.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"source-image")
    hash_index = _make_index(tmp_path)
    await hash_index.sync_incremental()
    hash_index.database_path.unlink()

    assert await hash_index.is_duplicate(b"source-image") is True
    assert hash_index.database_path.is_file()
    assert image_path.read_bytes() == b"source-image"


@pytest.mark.asyncio
async def test_corrupt_database_is_backed_up_and_rebuilt(tmp_path):
    image_path = tmp_path / "custom" / "33" / "source.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"source-image")
    hash_index = _make_index(tmp_path)
    await hash_index.sync_incremental()
    hash_index.database_path.write_bytes(b"not-a-sqlite-database")

    result = await hash_index.sync_incremental()

    assert result.rebuilt is True
    assert list(tmp_path.glob("gallery_hash_index.sqlite3.corrupt-*"))
    assert await hash_index.is_duplicate(b"source-image") is True
    assert image_path.read_bytes() == b"source-image"


@pytest.mark.asyncio
async def test_hash_counter_survives_deleting_one_duplicate_path(tmp_path):
    first_path = tmp_path / "toya" / "first.jpg"
    second_path = tmp_path / "custom" / "33" / "second.jpg"
    first_path.parent.mkdir(parents=True)
    second_path.parent.mkdir(parents=True)
    first_path.write_bytes(b"same-image")
    second_path.write_bytes(b"same-image")
    hash_index = _make_index(tmp_path)
    await hash_index.sync_incremental()

    first_path.unlink()
    await hash_index.sync_incremental()
    assert await hash_index.is_duplicate(b"same-image") is True

    second_path.unlink()
    await hash_index.sync_incremental()
    assert await hash_index.is_duplicate(b"same-image") is False


@pytest.mark.asyncio
async def test_save_updates_index_immediately_without_rescanning(monkeypatch, tmp_path):
    save_dir = tmp_path / "custom" / "月城"
    hash_index = _make_index(tmp_path)
    await hash_index.ensure_ready()

    def fail_scan():
        pytest.fail("ready index should not rescan during each save")

    monkeypatch.setattr(hash_index, "_scan_gallery_files", fail_scan)

    assert await hash_index.save_unique(save_dir, "first.jpg", b"new-image") is True
    assert await hash_index.save_unique(save_dir, "second.jpg", b"new-image") is False
    assert len(list(save_dir.glob("*.jpg"))) == 1

    restarted_index = _make_index(tmp_path)
    await restarted_index.sync_incremental()
    assert await restarted_index.is_duplicate(b"new-image") is True


@pytest.mark.asyncio
async def test_ten_minute_job_runs_incremental_sync(monkeypatch):
    sync_result = GalleryIndexSyncResult(indexed_count=12, updated_count=1)
    hash_index = SimpleNamespace(sync_incremental=AsyncMock(return_value=sync_result))
    monkeypatch.setattr(gallery, "GALLERY_HASH_INDEX", hash_index)

    await gallery.sync_gallery_hash_index()

    hash_index.sync_incremental.assert_awaited_once()
    assert gallery.sync_gallery_hash_index.__scheduled_job_args__ == ("interval",)
    assert gallery.sync_gallery_hash_index.__scheduled_job_kwargs__ == {
        "minutes": 10,
        "id": "sync_gallery_hash_index",
        "max_instances": 1,
        "coalesce": True,
    }
