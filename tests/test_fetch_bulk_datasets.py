"""fetch_bulk_datasets.py: CSV->Parquet conversion and the min-rows guard against a
truncated/garbage download. Does not hit the network - download() itself isn't exercised."""

import csv
import sys
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fetch_bulk_datasets import csv_to_parquet  # noqa: E402


def _write_csv(path: Path, rows: int) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["LEI", "Entity.LegalName"])
        for i in range(rows):
            writer.writerow([f"LEI{i}", f"Company {i}"])


def test_csv_to_parquet_converts_and_returns_row_count(tmp_path):
    csv_path = tmp_path / "data.csv"
    parquet_path = tmp_path / "data.parquet"
    _write_csv(csv_path, 10)

    row_count = csv_to_parquet(csv_path, parquet_path, min_rows=5)

    assert row_count == 10
    assert parquet_path.exists()
    con = duckdb.connect()
    assert con.execute(f"SELECT count(*) FROM read_parquet('{parquet_path}')").fetchone()[0] == 10


def test_csv_to_parquet_rejects_truncated_download(tmp_path):
    csv_path = tmp_path / "data.csv"
    parquet_path = tmp_path / "data.parquet"
    _write_csv(csv_path, 3)

    with pytest.raises(RuntimeError, match="only 3 rows"):
        csv_to_parquet(csv_path, parquet_path, min_rows=100)

    assert not parquet_path.exists()
