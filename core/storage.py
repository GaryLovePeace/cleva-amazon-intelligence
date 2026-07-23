from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


class SnapshotStore:
    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "cleva_intelligence.sqlite"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module TEXT NOT NULL,
                    label TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    row_count INTEGER NOT NULL,
                    data_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )

    def save_snapshot(
        self,
        module: str,
        label: str,
        data: pd.DataFrame,
        source_url: str = "",
        metadata: dict | None = None,
    ) -> int:
        payload = data.where(pd.notna(data), None).to_dict(orient="records")
        collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO snapshots
                (module, label, collected_at, source_url, row_count, data_json, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    module,
                    label,
                    collected_at,
                    source_url,
                    len(data),
                    json.dumps(payload, ensure_ascii=False, default=str),
                    json.dumps(metadata or {}, ensure_ascii=False, default=str),
                ),
            )
            return int(cursor.lastrowid)

    def list_snapshots(self, limit: int = 100) -> pd.DataFrame:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, module, label, collected_at, source_url, row_count
                FROM snapshots ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return pd.DataFrame([dict(row) for row in rows])

    def previous_snapshot(
        self, module: str, current: pd.DataFrame | None = None
    ) -> pd.DataFrame:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data_json FROM snapshots WHERE module=? ORDER BY id DESC LIMIT 20",
                (module,),
            ).fetchall()
        current_signature = self._rank_signature(current) if current is not None else None
        for row in rows:
            frame = pd.DataFrame(json.loads(row["data_json"]))
            if current_signature is not None and self._rank_signature(frame) == current_signature:
                continue
            return frame
        return pd.DataFrame()

    @staticmethod
    def _rank_signature(frame: pd.DataFrame | None) -> tuple:
        if frame is None or frame.empty or "asin" not in frame or "rank" not in frame:
            return tuple()
        pairs = frame[["asin", "rank"]].astype(object)
        pairs = pairs.where(pd.notna(pairs), None)
        return tuple(sorted((str(asin), rank) for asin, rank in pairs.itertuples(index=False)))

    def all_rows(self, module: str) -> pd.DataFrame:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT collected_at, data_json FROM snapshots
                WHERE module=? ORDER BY id ASC
                """,
                (module,),
            ).fetchall()
        frames = []
        for row in rows:
            frame = pd.DataFrame(json.loads(row["data_json"]))
            frame["_snapshot_at"] = row["collected_at"]
            frames.append(frame)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def summary(self) -> dict:
        snapshots = self.list_snapshots(limit=10000)
        if snapshots.empty:
            return {"total_snapshots": 0, "bsr_rows": 0, "intel_rows": 0, "sales_rows": 0}
        return {
            "total_snapshots": len(snapshots),
            "bsr_rows": int(
                snapshots.loc[snapshots["module"].isin(["wet_dry", "spot_cleaner"]), "row_count"].sum()
            ),
            "intel_rows": int(
                snapshots.loc[snapshots["module"] == "competitor", "row_count"].sum()
            ),
            "sales_rows": int(snapshots.loc[snapshots["module"] == "sales", "row_count"].sum()),
        }
