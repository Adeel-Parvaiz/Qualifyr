"""GLEIF Golden Copy discovery: SQL/mapping against a small synthetic Parquet fixture
(written in-test via duckdb, not the real multi-GB dataset), missing-file RuntimeError."""

import pytest

duckdb = pytest.importorskip("duckdb")

from gtm_engine.discovery.gleif_golden_copy import GLEIFGoldenCopyDiscovery, row_to_company


def _write_fixture(path):
    con = duckdb.connect()
    con.execute(f"""
        COPY (
            SELECT * FROM (VALUES
                ('LEI1', 'Acme Corp', 'PK', 'Karachi', '1 Main St', 'PK', 'ISSUED', 'ACTIVE'),
                ('LEI2', NULL, 'GB', 'London', '2 High St', 'GB', 'ISSUED', 'ACTIVE'),
                ('LEI3', 'Zenith Ltd', 'GB', 'London', '3 Low St', 'GB', 'ISSUED', 'ACTIVE')
            ) AS t("LEI", "Entity.LegalName", "Entity.LegalAddress.Country",
                   "Entity.LegalAddress.City", "Entity.LegalAddress.FirstAddressLine",
                   "Entity.LegalJurisdiction", "Registration.RegistrationStatus",
                   "Entity.EntityStatus")
        ) TO '{path}' (FORMAT PARQUET)
    """)
    con.close()


def test_row_to_company_maps_fields():
    row = {"lei": "LEI1", "legal_name": "Acme Corp", "legal_country": "PK",
           "legal_city": "Karachi", "legal_address_lines": "1 Main St",
           "jurisdiction": "PK", "registration_status": "ISSUED", "entity_status": "ACTIVE"}
    company = row_to_company(row)
    assert company.name == "Acme Corp"
    assert company.country == "PK"
    assert company.extra["lei"] == "LEI1"


def test_row_to_company_without_name_returns_none():
    assert row_to_company({"legal_name": None}) is None


async def test_discover_reads_fixture_and_filters_by_country(settings, tmp_path):
    fixture_path = tmp_path / "golden_copy.parquet"
    _write_fixture(fixture_path)
    settings.enable_gleif_golden_copy = True
    settings.gleif_golden_copy_path = fixture_path

    from gtm_engine.config.schema import CampaignConfig, GeographyConfig

    campaign = CampaignConfig(campaign_id="t", name="t", offer="x", geography=GeographyConfig(country_codes=["GB"]))
    discovery = GLEIFGoldenCopyDiscovery(settings)
    found = [c async for c in discovery.discover(campaign)]
    # LEI2 has a NULL legal name and is dropped by row_to_company.
    assert len(found) == 1
    assert found[0].name == "Zenith Ltd"


async def test_discover_disabled_by_default_setting(settings, tmp_path):
    from gtm_engine.config.schema import CampaignConfig

    settings.enable_gleif_golden_copy = False
    campaign = CampaignConfig(campaign_id="t", name="t", offer="x")
    discovery = GLEIFGoldenCopyDiscovery(settings)
    found = [c async for c in discovery.discover(campaign)]
    assert found == []


async def test_discover_raises_on_missing_file(settings, tmp_path):
    from gtm_engine.config.schema import CampaignConfig

    settings.enable_gleif_golden_copy = True
    settings.gleif_golden_copy_path = tmp_path / "does_not_exist.parquet"
    campaign = CampaignConfig(campaign_id="t", name="t", offer="x")
    discovery = GLEIFGoldenCopyDiscovery(settings)
    with pytest.raises(RuntimeError, match="fetch_bulk_datasets"):
        [c async for c in discovery.discover(campaign)]
