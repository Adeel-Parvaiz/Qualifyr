from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import yaml

from gtm_engine.config.schema import CampaignConfig, DefaultRules, EngineSettings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path | None = None) -> int:
    """Read KEY=VALUE lines from .env into the environment for local runs. Values already
    set in the real environment win, so CI secrets are never overridden by a stale file."""
    path = path or PROJECT_ROOT / ".env"
    if not path.exists():
        return 0
    loaded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value.startswith(('"', "'")):
            # Preserve hashes and spaces inside a quoted value.
            end = value.find(value[0], 1)
            if end != -1:
                value = value[1:end]
        else:
            # A whitespace-separated hash starts a dotenv comment. A hash in a
            # password (e.g. password#123) is part of the value.
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


load_dotenv()
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULTS_DIR = CONFIG_DIR / "defaults"


def is_serverless() -> bool:
    """True on Vercel / AWS Lambda, where the deployment bundle is mounted read-only."""
    return bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def runtime_dir() -> Path:
    """A directory this process may actually write to.

    Everything under PROJECT_ROOT is read-only on Vercel; only the temp dir is writable,
    and only for the life of the container. Callers must therefore treat what they put
    here as scratch - anything durable belongs in Postgres. That is already true of the
    two things that land here (the dry-run outbox and the API's ledger copy): real sends
    run in GitHub Actions, which has a writable checkout and commits the ledger back.
    """
    base = Path(tempfile.gettempdir()) / "gtm" if is_serverless() else PROJECT_ROOT / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_campaign(path: str | Path) -> CampaignConfig:
    path = Path(path)
    data = _read_yaml(path)
    if not data:
        raise FileNotFoundError(f"campaign config not found or empty: {path}")
    if data.get("seed_csv"):
        seed = Path(data["seed_csv"])
        if not seed.is_absolute():
            seed = (path.parent / seed).resolve()
        data["seed_csv"] = seed
    return CampaignConfig.model_validate(data)


def resolve_campaign(arg: str | Path, database_url: str | None = None) -> CampaignConfig:
    """Load a campaign from either a YAML file path or a campaign_id stored in the DB.

    User-created campaigns live in Postgres (the `campaigns` table), not on disk, so the
    CLI and the Actions runner must accept an id as readily as a path. A value that names
    an existing file is read as YAML; anything else is treated as a campaign_id and looked
    up in the database. This is what lets `gtm run <campaign_id>` work for a campaign a user
    created in the UI, with no file committed to the repo."""
    p = Path(arg)
    if p.exists():
        return load_campaign(p)
    from gtm_engine.storage.database import Database

    db = Database(database_url)
    try:
        cfg = db.campaign_config(str(arg))
    finally:
        db.close()
    if not cfg:
        raise FileNotFoundError(f"campaign not found as a file or a DB campaign_id: {arg!r}")
    return CampaignConfig.model_validate(cfg)


def slugify_campaign_id(name: str, existing: set[str] | None = None) -> str:
    """A stable, unique, url-safe id from a campaign name. 'Retail & Apparel, Lahore' ->
    'retail-apparel-lahore'; collisions get a -2, -3 suffix."""
    import re

    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "campaign"
    if not existing or base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def load_defaults(defaults_dir: Path = DEFAULTS_DIR) -> DefaultRules:
    merged: dict = {}
    for file in sorted(defaults_dir.glob("*.yaml")):
        merged.update(_read_yaml(file))
    return DefaultRules.model_validate(merged)


def load_settings(path: Path | None = None) -> EngineSettings:
    data = _read_yaml(path or CONFIG_DIR / "engine.yaml")
    # Environment overrides: GTM_DB_PATH, GTM_CONCURRENCY, ...
    for key in EngineSettings.model_fields:
        env_val = os.environ.get(f"GTM_{key.upper()}")
        if env_val is not None:
            data[key] = env_val
    settings = EngineSettings.model_validate(data)
    if settings.google_places_api_key and not settings.enable_places_enrichment:
        settings.enable_places_enrichment = True
    for attr in ("db_path", "export_dir"):
        p = getattr(settings, attr)
        if not p.is_absolute():
            setattr(settings, attr, PROJECT_ROOT / p)
    return settings
