"""One-time download of a bulk discovery dataset into the local Parquet file the matching
discovery class reads (e.g. `gleif_golden_copy.py` -> `settings.gleif_golden_copy_path`).

Not run by the pipeline automatically - these datasets are large (GLEIF's Golden Copy LEI-CDF
file is 1+ GB as CSV) and change infrequently, so refreshing them is a deliberate, manual step.

Downloads to a `.tmp` sibling first, sanity-checks the row count (refuses to proceed on a
near-empty file - a truncated download or a changed URL should never silently replace a good
dataset with junk), converts CSV -> Parquet via DuckDB, then atomically renames into place.

Usage: python scripts/fetch_bulk_datasets.py --dataset gleif [--url URL] [--min-rows N]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from urllib.request import urlretrieve

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gtm_engine.config import load_settings  # noqa: E402

log = logging.getLogger(__name__)

# GLEIF publishes the latest Golden Copy LEI-CDF concatenated file at a stable "latest"
# redirect; the exact dated URL changes on every publish cycle.
GLEIF_GOLDEN_COPY_URL = (
    "https://goldencopy.gleif.org/api/v2/golden-copies/publishes/lei2/latest"
)

DATASETS = {
    "gleif": {
        "url": GLEIF_GOLDEN_COPY_URL,
        "min_rows": 100_000,
        "settings_path_attr": "gleif_golden_copy_path",
    },
}


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".download")
    log.info("downloading %s -> %s", url, tmp)
    urlretrieve(url, tmp)
    tmp.rename(dest)


def csv_to_parquet(csv_path: Path, parquet_path: Path, min_rows: int) -> int:
    import duckdb

    con = duckdb.connect()
    row_count = con.execute(
        f"SELECT count(*) FROM read_csv_auto('{csv_path}', all_varchar=true)"
    ).fetchone()[0]
    if row_count < min_rows:
        raise RuntimeError(
            f"downloaded file has only {row_count} rows (expected >= {min_rows}); "
            "refusing to overwrite the existing dataset - the download may be truncated "
            "or the source URL may have changed format"
        )
    tmp_parquet = parquet_path.with_suffix(parquet_path.suffix + ".tmp")
    tmp_parquet.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"COPY (SELECT * FROM read_csv_auto('{csv_path}', all_varchar=true)) "
        f"TO '{tmp_parquet}' (FORMAT PARQUET)"
    )
    con.close()
    tmp_parquet.rename(parquet_path)
    return row_count


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, choices=sorted(DATASETS))
    parser.add_argument("--url", default=None, help="override the default source URL")
    parser.add_argument("--min-rows", type=int, default=None)
    args = parser.parse_args()

    spec = DATASETS[args.dataset]
    url = args.url or spec["url"]
    min_rows = args.min_rows or spec["min_rows"]

    settings = load_settings()
    parquet_path = getattr(settings, spec["settings_path_attr"])

    csv_path = parquet_path.with_suffix(".csv")
    download(url, csv_path)
    row_count = csv_to_parquet(csv_path, parquet_path, min_rows)
    csv_path.unlink(missing_ok=True)
    log.info("%s: %d rows -> %s", args.dataset, row_count, parquet_path)


if __name__ == "__main__":
    main()
