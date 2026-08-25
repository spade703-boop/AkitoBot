"""SQLite-backed SHA-256 index for fixed and custom gallery images."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import sqlite3
import uuid

import aiosqlite
from nonebot.log import logger

GALLERY_HASH_INDEX_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class GalleryImageRecord:
    relative_path: str
    file_size: int
    mtime_ns: int
    sha256_digest: bytes


@dataclass(frozen=True)
class GalleryIndexSyncResult:
    indexed_count: int
    updated_count: int = 0
    deleted_count: int = 0
    failed_count: int = 0
    rebuilt: bool = False


class GalleryIndexSchemaError(RuntimeError):
    pass


class GalleryHashIndex:
    def __init__(
        self,
        image_root: Path,
        database_path: Path,
        fixed_storage_keys: tuple[str, ...],
        image_suffixes: set[str],
    ) -> None:
        self.image_root = image_root
        self.database_path = database_path
        self.fixed_storage_keys = fixed_storage_keys
        self.image_suffixes = {suffix.casefold() for suffix in image_suffixes}
        self.lock = asyncio.Lock()
        self._hash_counts: Counter[bytes] = Counter()
        self._ready = False

    @property
    def indexed_count(self) -> int:
        return sum(self._hash_counts.values())

    async def ensure_ready(self) -> GalleryIndexSyncResult:
        async with self.lock:
            return await self._ensure_ready_locked()

    async def sync_incremental(self) -> GalleryIndexSyncResult:
        async with self.lock:
            return await self._sync_incremental_locked()

    async def rebuild(self) -> GalleryIndexSyncResult:
        async with self.lock:
            backup_invalid = False
            if self.database_path.exists():
                try:
                    await self._read_database()
                except (GalleryIndexSchemaError, sqlite3.DatabaseError):
                    backup_invalid = True
            return await self._rebuild_locked(backup_invalid=backup_invalid)

    async def is_duplicate(self, image_data: bytes) -> bool:
        digest = await asyncio.to_thread(_hash_bytes, image_data)
        async with self.lock:
            await self._ensure_ready_locked()
            return self._hash_counts[digest] > 0

    async def save_unique(self, save_dir: Path, file_name: str, image_data: bytes) -> bool:
        digest = await asyncio.to_thread(_hash_bytes, image_data)
        async with self.lock:
            await self._ensure_ready_locked()
            if self._hash_counts[digest] > 0:
                return False

            save_path = await asyncio.to_thread(_write_unique_file, save_dir, file_name, image_data)
            stat_result = await asyncio.to_thread(save_path.stat)
            record = GalleryImageRecord(
                relative_path=self._relative_path(save_path),
                file_size=stat_result.st_size,
                mtime_ns=stat_result.st_mtime_ns,
                sha256_digest=digest,
            )
            try:
                await self._upsert_record(record)
            except (GalleryIndexSchemaError, sqlite3.DatabaseError, OSError) as exc:
                logger.warning(f"图库哈希索引写入异常，正在自动重建: {exc}")
                self._ready = False
                await self._rebuild_locked(backup_invalid=self.database_path.exists())
            else:
                self._hash_counts[digest] += 1
            return True

    async def _ensure_ready_locked(self) -> GalleryIndexSyncResult:
        if self._ready and self.database_path.is_file():
            return GalleryIndexSyncResult(indexed_count=self.indexed_count)
        return await self._sync_incremental_locked()

    async def _sync_incremental_locked(self) -> GalleryIndexSyncResult:
        try:
            existing_records = await self._read_database()
        except FileNotFoundError:
            logger.info("图库哈希索引不存在，正在根据原图片自动创建")
            return await self._rebuild_locked(backup_invalid=False)
        except (GalleryIndexSchemaError, sqlite3.DatabaseError) as exc:
            logger.warning(f"图库哈希索引异常，正在根据原图片自动重建: {exc}")
            return await self._rebuild_locked(backup_invalid=True)

        snapshot = await asyncio.to_thread(self._scan_gallery_files)
        final_records = dict(existing_records)
        failed_count = 0

        for relative_path in set(existing_records) - set(snapshot):
            final_records.pop(relative_path, None)

        for relative_path, (image_path, file_size, mtime_ns) in snapshot.items():
            existing = existing_records.get(relative_path)
            if existing and existing.file_size == file_size and existing.mtime_ns == mtime_ns:
                continue
            try:
                final_records[relative_path] = await asyncio.to_thread(
                    self._hash_file_record,
                    relative_path,
                    image_path,
                )
            except OSError as exc:
                final_records.pop(relative_path, None)
                failed_count += 1
                logger.warning(f"图库图片读取失败，暂未登记索引: {image_path}: {exc}")

        deleted_paths = set(existing_records) - set(final_records)
        updated_records = [
            record
            for relative_path, record in final_records.items()
            if existing_records.get(relative_path) != record
        ]
        try:
            await self._apply_changes(deleted_paths, updated_records)
        except (GalleryIndexSchemaError, sqlite3.DatabaseError) as exc:
            logger.warning(f"图库哈希索引同步异常，正在自动重建: {exc}")
            return await self._rebuild_locked(backup_invalid=self.database_path.exists())

        self._replace_memory_index(final_records)
        self._ready = True
        return GalleryIndexSyncResult(
            indexed_count=len(final_records),
            updated_count=len(updated_records),
            deleted_count=len(deleted_paths),
            failed_count=failed_count,
        )

    async def _rebuild_locked(self, *, backup_invalid: bool) -> GalleryIndexSyncResult:
        self._ready = False
        snapshot = await asyncio.to_thread(self._scan_gallery_files)
        records: dict[str, GalleryImageRecord] = {}
        failed_count = 0
        for relative_path, (image_path, _, _) in snapshot.items():
            try:
                records[relative_path] = await asyncio.to_thread(
                    self._hash_file_record,
                    relative_path,
                    image_path,
                )
            except OSError as exc:
                failed_count += 1
                logger.warning(f"图库图片读取失败，暂未登记索引: {image_path}: {exc}")

        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.database_path.with_name(
            f".{self.database_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            await self._create_database(temporary_path, records.values())
            await asyncio.to_thread(
                self._replace_database,
                temporary_path,
                backup_invalid,
            )
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

        self._replace_memory_index(records)
        self._ready = True
        return GalleryIndexSyncResult(
            indexed_count=len(records),
            updated_count=len(records),
            failed_count=failed_count,
            rebuilt=True,
        )

    def _scan_gallery_files(self) -> dict[str, tuple[Path, int, int]]:
        roots = [self.image_root / storage_key for storage_key in self.fixed_storage_keys]
        roots.append(self.image_root / "custom")
        snapshot: dict[str, tuple[Path, int, int]] = {}
        for root in roots:
            if not root.exists():
                continue
            for image_path in root.rglob("*"):
                if not image_path.is_file() or image_path.suffix.casefold() not in self.image_suffixes:
                    continue
                try:
                    stat_result = image_path.stat()
                    relative_path = self._relative_path(image_path)
                except (OSError, ValueError) as exc:
                    logger.warning(f"图库图片信息读取失败，暂未登记索引: {image_path}: {exc}")
                    continue
                snapshot[relative_path] = (image_path, stat_result.st_size, stat_result.st_mtime_ns)
        return dict(sorted(snapshot.items()))

    def _hash_file_record(self, relative_path: str, image_path: Path) -> GalleryImageRecord:
        digest = sha256()
        with open(image_path, "rb") as image_file:
            before = os.fstat(image_file.fileno())
            for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(image_file.fileno())
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise OSError("文件在计算哈希时发生变化")
        return GalleryImageRecord(
            relative_path=relative_path,
            file_size=after.st_size,
            mtime_ns=after.st_mtime_ns,
            sha256_digest=digest.digest(),
        )

    def _relative_path(self, image_path: Path) -> str:
        return image_path.relative_to(self.image_root).as_posix()

    async def _read_database(self) -> dict[str, GalleryImageRecord]:
        if not self.database_path.is_file():
            raise FileNotFoundError(self.database_path)
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute("PRAGMA busy_timeout = 5000")
            async with connection.execute("PRAGMA quick_check") as cursor:
                check_result = await cursor.fetchone()
            if not check_result or check_result[0] != "ok":
                raise GalleryIndexSchemaError("SQLite 完整性检查失败")
            async with connection.execute(
                "SELECT value FROM gallery_index_meta WHERE key = 'schema_version'"
            ) as cursor:
                version_row = await cursor.fetchone()
            if not version_row or version_row[0] != str(GALLERY_HASH_INDEX_SCHEMA_VERSION):
                raise GalleryIndexSchemaError("图库索引版本不兼容")
            async with connection.execute(
                "SELECT relative_path, file_size, mtime_ns, sha256 FROM gallery_images"
            ) as cursor:
                rows = await cursor.fetchall()

        records: dict[str, GalleryImageRecord] = {}
        for relative_path, file_size, mtime_ns, digest in rows:
            if not _valid_relative_path(relative_path) or not isinstance(digest, bytes) or len(digest) != 32:
                raise GalleryIndexSchemaError("图库索引包含无效记录")
            records[relative_path] = GalleryImageRecord(
                relative_path=relative_path,
                file_size=int(file_size),
                mtime_ns=int(mtime_ns),
                sha256_digest=digest,
            )
        return records

    async def _create_database(
        self,
        database_path: Path,
        records: Iterable[GalleryImageRecord],
    ) -> None:
        async with aiosqlite.connect(database_path) as connection:
            await connection.execute("PRAGMA journal_mode = DELETE")
            await connection.execute("PRAGMA synchronous = NORMAL")
            await connection.executescript(
                """
                CREATE TABLE gallery_index_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE gallery_images (
                    relative_path TEXT PRIMARY KEY,
                    file_size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    sha256 BLOB NOT NULL
                );
                CREATE INDEX gallery_images_sha256_idx ON gallery_images (sha256);
                """
            )
            await connection.execute(
                "INSERT INTO gallery_index_meta (key, value) VALUES ('schema_version', ?)",
                (str(GALLERY_HASH_INDEX_SCHEMA_VERSION),),
            )
            await connection.executemany(
                """
                INSERT INTO gallery_images (relative_path, file_size, mtime_ns, sha256)
                VALUES (?, ?, ?, ?)
                """,
                (
                    (
                        record.relative_path,
                        record.file_size,
                        record.mtime_ns,
                        record.sha256_digest,
                    )
                    for record in records
                ),
            )
            await connection.commit()

    async def _apply_changes(
        self,
        deleted_paths: set[str],
        updated_records: list[GalleryImageRecord],
    ) -> None:
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute("PRAGMA busy_timeout = 5000")
            if deleted_paths:
                await connection.executemany(
                    "DELETE FROM gallery_images WHERE relative_path = ?",
                    ((relative_path,) for relative_path in deleted_paths),
                )
            if updated_records:
                await connection.executemany(
                    """
                    INSERT INTO gallery_images (relative_path, file_size, mtime_ns, sha256)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(relative_path) DO UPDATE SET
                        file_size = excluded.file_size,
                        mtime_ns = excluded.mtime_ns,
                        sha256 = excluded.sha256
                    """,
                    (
                        (
                            record.relative_path,
                            record.file_size,
                            record.mtime_ns,
                            record.sha256_digest,
                        )
                        for record in updated_records
                    ),
                )
            await connection.commit()

    async def _upsert_record(self, record: GalleryImageRecord) -> None:
        await self._apply_changes(set(), [record])

    def _replace_database(self, temporary_path: Path, backup_invalid: bool) -> None:
        backup_path: Path | None = None
        if backup_invalid and self.database_path.exists():
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = self.database_path.with_name(
                f"{self.database_path.name}.corrupt-{timestamp}-{uuid.uuid4().hex[:8]}"
            )
            os.replace(self.database_path, backup_path)
        try:
            os.replace(temporary_path, self.database_path)
        except OSError:
            if backup_path is not None and backup_path.exists() and not self.database_path.exists():
                os.replace(backup_path, self.database_path)
            raise

    def _replace_memory_index(self, records: dict[str, GalleryImageRecord]) -> None:
        self._hash_counts = Counter(record.sha256_digest for record in records.values())


def _hash_bytes(image_data: bytes) -> bytes:
    return sha256(image_data).digest()


def _write_unique_file(save_dir: Path, file_name: str, image_data: bytes) -> Path:
    save_dir.mkdir(parents=True, exist_ok=True)
    source_path = Path(file_name)
    collision_index = 0
    while True:
        suffix = "" if collision_index == 0 else f"_{collision_index}"
        save_path = save_dir / f"{source_path.stem}{suffix}{source_path.suffix}"
        try:
            with open(save_path, "xb") as image_file:
                image_file.write(image_data)
            return save_path
        except FileExistsError:
            collision_index += 1


def _valid_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts
