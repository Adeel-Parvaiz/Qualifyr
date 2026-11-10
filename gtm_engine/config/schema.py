"""Configuration schema. Everything that drives qualification and scoring lives here,
not in code, so an ICP change never requires a code change."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class GeographyConfig(BaseModel):
    countries: list[str] = Field(default_factory=lambda: ["Pakistan"])
    # Provinces / states, searched as broader areas than a single city (a province geocodes
    # to a bbox that covers many towns). Optional middle tier between country and city.
    provinces: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    # Specific localities/sectors/neighborhoods within a city (e.g. G-7, DHA, Saddar).
    areas: list[str] = Field(default_factory=list)
    # ISO 3166-1 alpha-2 codes (e.g. "PK", "GB", "US"), for sources that need a precise
    # country filter rather than free-text `countries` matching (GLEIF, EDGAR, Companies
    # House, Wikidata, PDL). Independent of `countries`; set either or both.
    country_codes: list[str] = Field(default_factory=list)

    @field_validator("countries", "provinces", "cities", "areas")
    @classmethod
    def _strip(cls, values: list[str]) -> list[str]:
        return [v.strip() for v in values if v and v.strip()]

    @field_validator("country_codes")
    @classmethod
    def _upper(cls, values: list[str]) -> list[str]:
        return [v.strip().upper() for v in values if v and v.strip()]

    def search_areas(self, include_country_fallback: bool = False) -> list[str]:
        """Places to geocode into search bboxes: provinces first (broader), then cities.
        De-duped, order preserved, so a run does not scan the same area twice.

        include_country_fallback=True additionally falls back to `countries` when neither
        provinces nor cities is set, so a query that only names a country (e.g. "grocery
        stores in France") still gets a geocodable bbox for OSM/Overture map discovery
        instead of silently disabling it. Left off by default: country-wide text-search
        queries (e.g. sparse-fallback "<category> in Pakistan") are too broad to be useful,
        so only the map-discovery geocoding call sites opt in."""
        seen: set[str] = set()
        out: list[str] = []
        candidates = [*self.provinces, *self.cities]
        if not candidates and include_country_fallback:
            candidates = list(self.countries)
        for area in candidates:
            key = area.lower()
            if key not in seen:
                seen.add(key)
                out.append(area)
        return out


class ScoringWeights(BaseModel):
    """Maximum points per dimension. Must sum to 100.

    The decomposed transparent score (reference xlsx formula):
      review_band 0-30 + rating 0-10 + proximity_tier 0-15
      + online_gap 0-25 + pain_evidence 0-20 = 100
    """

    review_band: int = 30
    rating: int = 10
    proximity_tier: int = 15
    online_gap: int = 25
    pain_evidence: int = 20

    def total(self) -> int:
        return (
            self.review_band
            + self.rating
            + self.proximity_tier
            + self.online_gap
            + self.pain_evidence
        )


class RoutingThresholds(BaseModel):
    high_priority: int = 55
    qualified: int = 40
    review: int = 20


class CampaignConfig(BaseModel):
    campaign_id: str
    name: str
    offer: str = Field(description="What we sell; used only for context and personalization.")
    target_industries: list[str] = Field(default_factory=list)
    geography: GeographyConfig = Field(default_factory=GeographyConfig)
    company_size: str | None = None
    target_roles: list[str] = Field(default_factory=list)
    # Terms that indicate an end-buyer company for this campaign (retailer, clinic, ...).
    buyer_keywords: list[str] = Field(default_factory=list)
    # Campaign-specific vendor terms; merged with the global default negative list.
    negative_keywords: list[str] = Field(default_factory=list)
    # Vendor categories the campaign explicitly wants anyway (rare; e.g. targeting agencies).
    allowed_vendor_keywords: list[str] = Field(default_factory=list)
    # OSM tag filters, e.g. ["shop=*", "shop=clothes", "amenity=clinic"].
    osm_categories: list[str] = Field(default_factory=list)
    # Overture category substrings, e.g. ["clothing", "shoe_store", "supermarket"].
    overture_categories: list[str] = Field(default_factory=list)
    # (Foursquare support removed 2026-10-10 — the keyless upstream dataset was discontinued.)
    # Web-search discovery queries (E2). Normally derived from the offer at run time; the user
    # rarely sets these directly.
    search_queries: list[str] = Field(default_factory=list)
    # Optional seed list of companies/domains supplied by the user.
    seed_csv: Path | None = None
    # Chamber directories (Pakistan): "kcci" today. Member names are filtered by the campaign's
    # industry/buyer terms plus these extra name keywords (directories carry no sector field).
    chamber_sources: list[str] = Field(default_factory=list)
    chamber_name_keywords: list[str] = Field(default_factory=list)
    # Intent sources: "ppra" turns organisations tendering for the offer into leads.
    intent_sources: list[str] = Field(default_factory=list)
    # Terms that describe what we sell, matched against tender text (in addition to industries).
    intent_keywords: list[str] = Field(default_factory=list)
    # GLEIF LEI search queries (company name fragments).
    gleif_lei_queries: list[str] = Field(default_factory=list)
    # Wikidata SPARQL industry filters (matched against P452 industry labels).
    wikidata_industries: list[str] = Field(default_factory=list)
    # UK Companies House SIC codes, e.g. ["47910"] (retail sale via internet).
    companies_house_sic_codes: list[str] = Field(default_factory=list)
    # US SEC EDGAR SIC codes, e.g. ["5961"] (catalog/mail-order houses).
    edgar_sic_codes: list[str] = Field(default_factory=list)
    min_score: int = 40
    # max_companies is the user-visible per-run lead target (renamed semantics 2026-10-10 for P3).
    # The user chooses this via the "leads this run" dropdown (3/5/10/...). The pipeline will
    # stop as soon as it has that many outreach_ready leads, OR exhausts the expansion ceiling
    # below, whichever comes first. No more infinite loops: if a 50-lead run finds 33, it
    # stops at 33 and reports partial.
    max_companies: int = 10
    # Guaranteed floor of qualified + outreach-ready leads the run should try to deliver,
    # expanding into the already-discovered pool beyond max_companies if the first pass falls
    # short. Unset (None) -> Pipeline.run() uses `max_companies` as the floor. Explicit 0 opts
    # out of expansion entirely (today's single-pass behavior). Capped at max_companies now
    # (P3): a run can never produce more than max_companies outreach_ready leads.
    min_outreach_ready: int | None = None
    # P3 (2026-10-10): internal safety ceiling, not user-settable. The expansion loop may
    # process at most max_companies * MAX_EXPANSION_MULTIPLIER_INTERNAL companies before
    # giving up and reporting partial. Hard-coded to 3 so a 10-lead run never crawls more
    # than 30 companies looking for them. Replaces the former `max_expansion_multiplier`
    # field which was user-settable (bad: user could ask for 10× scraping).
    max_expansion_multiplier: float = 3.0
    max_pages_per_site: int = 6
    allow_multiple_contacts_per_company: bool = False
    # Drop branches of national/international chains (OSM `brand` tag): decisions are not
    # made at the outlet and the only public contact is a customer-care mailbox.
    exclude_chains: bool = False
    # Post-scoring hard filters extracted from NL campaign descriptions.
    # Keys: min_google_reviews (int), max_proximity_tier (int),
    # require_online_gap (list of gap labels like "no_website", "no_app").
    hard_filters: dict = Field(default_factory=dict)
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    routing: RoutingThresholds = Field(default_factory=RoutingThresholds)

    @field_validator("weights")
    @classmethod
    def _weights_sum(cls, w: ScoringWeights) -> ScoringWeights:
        if w.total() != 100:
            raise ValueError(f"scoring weights must sum to 100, got {w.total()}")
        return w

    @field_validator(
        "target_industries", "target_roles", "buyer_keywords", "negative_keywords",
        "allowed_vendor_keywords", "osm_categories", "overture_categories", "chamber_sources", "chamber_name_keywords", "intent_sources", "intent_keywords",
        "wikidata_industries",
    )
    @classmethod
    def _lower(cls, values: list[str]) -> list[str]:
        return [v.strip().lower() for v in values if v and v.strip()]


class DefaultRules(BaseModel):
    """Global rule sets shipped in config/defaults/*.yaml. Campaigns extend, never replace."""

    negative_keywords: list[str] = Field(default_factory=list)
    # Self-descriptions typical of service sellers ("our clients include", "hire us").
    vendor_phrases: list[str] = Field(default_factory=list)
    # Non-English translations of the two lists above, keyed by ISO 639-1 language code.
    # Applied on top of the English lists when the crawled page's detected language has an
    # entry here; campaign-specific buyer terms are not pre-translatable this way and use
    # the LLM grounded fallback in llm/tasks.py instead. See vendor_rules_i18n.yaml.
    negative_keywords_i18n: dict[str, list[str]] = Field(default_factory=dict)
    vendor_phrases_i18n: dict[str, list[str]] = Field(default_factory=dict)
    # Discovery categories (OSM tags) that describe sellers of services by definition.
    vendor_categories: list[str] = Field(default_factory=list)
    buyer_role_whitelist: list[str] = Field(default_factory=list)
    role_blacklist: list[str] = Field(default_factory=list)
    generic_email_prefixes: list[str] = Field(default_factory=list)
    buying_signal_keywords: dict[str, list[str]] = Field(default_factory=dict)
    pain_signal_keywords: dict[str, list[str]] = Field(default_factory=dict)
    technology_markers: dict[str, list[str]] = Field(default_factory=dict)
    geography_tiers: dict[str, list[str]] = Field(default_factory=dict)
    intent_rfq_phrases: list[str] = Field(default_factory=list)
    intent_hiring_roles: list[str] = Field(default_factory=list)
    # GTM intelligence (job boards / GitHub / press): phrases matched against press/RSS
    # entry titles, grouped by what kind of event they signal.
    press_signal_keywords: dict[str, list[str]] = Field(default_factory=dict)
    # Job-board roles that imply growth/budget rather than routine backfill.
    job_growth_roles: list[str] = Field(default_factory=list)


class EngineSettings(BaseModel):
    """Runtime settings. Loaded from config/engine.yaml, overridable via env GTM_*."""

    db_path: Path = Path("data/gtm.sqlite")
    # Postgres connection string (Supabase pooler URL). Required in any deployed
    # environment; storage.Database refuses to construct without it.
    database_url: str | None = None
    export_dir: Path = Path("data/exports")
    user_agent: str = "GTMLeadEngine/0.1 (+business research; contact via site form)"
    request_timeout_s: float = 15.0
    # Hard cap on a single response body. A handful of 50 MB pages in one batch is enough
    # to exhaust memory; no company website needs more than a few MB of HTML.
    max_response_bytes: int = 4_000_000
    # Courtesy delay between requests to the SAME host. Protects shared external services
    # (Overpass, Nominatim) that many runs/users hit in common - those need real
    # rate-limit respect. Left deliberately conservative as the global default.
    per_host_delay_s: float = 2.0
    # A company's own website is a different story: it is hit a handful of times (home,
    # about, contact) in one run by nobody else, not a shared rate-limited resource, so it
    # does not need the same 2s-per-request courtesy. This was the single biggest per-company
    # latency driver - 2-6 page fetches per company at 2s apart added 4-12s to every company,
    # regardless of concurrency, since they all hit the same host.
    site_crawl_delay_s: float = 0.3
    max_retries: int = 2
    # After this many consecutive failures, stop calling a host for the rest of the run:
    # a dead or hostile host must not consume the batch's time in retries.
    host_failure_limit: int = 3
    # Hard ceiling on the time spent on one company's website.
    per_company_timeout_s: float = 90.0
    # Companies are almost always on different hosts, so the per_host_delay_s throttle above
    # does not limit cross-company parallelism - raised from 4 now that the real per-company
    # bottleneck (site_crawl_delay_s above) is fixed.
    concurrency: int = 6
    respect_robots: bool = True
    # SSRF guard: refuse to fetch URLs whose host is (or resolves to) a non-public address -
    # loopback, private, link-local, cloud-metadata - including IPv6-mapped IPv4 forms. Domains
    # are resolved and checked before the request, which stops a public-looking name that points
    # at a private IP; resolution failures fail open (no address to reach = no SSRF). On for
    # safety; a self-hoster crawling an internal mirror can turn it off. It does not pin the
    # resolved address, so network-level egress rules are still the backstop against DNS
    # rebinding between this check and the connection.
    block_private_hosts: bool = True
    # Render JS-only sites with headless Chromium when static HTTP returns an empty shell.
    # Needs the `browser` extra; off by default because no target site has needed it yet.
    enable_browser_fallback: bool = False
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    # Tried in order when the primary returns an error or rate-limits (shared public instances).
    overpass_mirrors: list[str] = Field(default_factory=lambda: [
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.private.coffee/api/interpreter",
    ])
    overpass_timeout_s: int = 90
    enable_search_fallback: bool = True
    search_delay_s: float = 5.0
    # Web-search discovery (E2): find companies from the offer's queries, not just map tags.
    # Bounded per run to cap Brave spend; the run's max_companies still caps what gets processed.
    enable_web_search_discovery: bool = True
    web_search_max_queries_per_run: int = 6
    # Sparse-area fallback: when the primary geo sources (Overture + Foursquare + OSM) collectively
    # yield fewer than `sparse_discovery_threshold` companies for the campaign's niche-city combo,
    # synthesize web-search queries ("<category> in <city>") and run WebSearchDiscovery against
    # them even if the campaign carries no `search_queries` of its own. Fixes the "niche category
    # in a small town" case (vet clinics in Wah Cantt returning only 2-5 rows from Overture/OSM).
    # Capped to `sparse_fallback_max_queries` extra queries per run to bound Brave spend.
    enable_sparse_fallback: bool = True
    sparse_discovery_threshold: int = 10
    sparse_fallback_max_queries: int = 5
    # External signals (all keyless): RDAP domain age, GDELT news mentions (1 req / 5.5 s).
    enable_domain_age: bool = True
    enable_news_signals: bool = True
    news_max_companies_per_run: int = 40
    dns_timeout_s: float = 5.0
    # Mailbox-level email verification: direct | reacher | auto | off (see validation/verifier.py).
    # Default is direct SMTP (own-infrastructure, no paid API) - degrades to MX-only when port 25
    # is blocked. Hunter was removed on 2026-10-09.
    email_verification: str = "direct"
    reacher_url: str | None = None
    # Try first.last@ style candidates for a named decision-maker when only a generic mailbox is public.
    discover_decision_maker_email: bool = True
    # Optional LLM layer (docs/DIRECTION.md). Off by default; deterministic paths always run.
    enable_llm: bool = False
    llm_provider: str = "auto"      # auto | ollama | groq | gemini
    llm_model: str | None = None
    enable_intent_signals: bool = True
    # GTM intelligence (all keyless, budget-limited like domain age / news): job-board
    # postings (Greenhouse/Lever), GitHub org activity, press/RSS mentions.
    enable_job_board_signals: bool = True
    job_board_max_companies_per_run: int = 40
    enable_github_signals: bool = True
    github_max_companies_per_run: int = 30
    enable_press_signals: bool = True
    press_max_companies_per_run: int = 40
    enable_places_enrichment: bool = False
    google_places_api_key: str | None = None
    places_max_companies_per_run: int = 20
    enable_review_text: bool = False
    # Invisible per-user lead personalization (docs/PERSONALIZATION_PLAN.md). The grounded
    # total_score/priority are never touched; this only biases a separate rank_score used for
    # surfacing order. Kill-switch: false forces bias=0 everywhere, i.e. today's exact behavior.
    enable_personalization: bool = True
    personalization_max_bias: float = 8.0
    personalization_learning_rate: float = 0.05
    # Proximity scoring anchor: the user's office/home location.
    # Companies are ranked by distance from this point (Tier 1/2/3).
    # When unset, all companies get Tier 1 (same-city assumption).
    anchor_lat: float | None = None
    anchor_lon: float | None = None
    proximity_tier1_km: float = 5.0
    proximity_tier2_km: float = 15.0
    # Website finder (Brave) is a metered API on a small monthly free credit. Cap the searches
    # per run so a large max_companies cannot drain the month's budget in one go; companies
    # past the cap keep whatever website discovery already gave them.
    website_finder_max_per_run: int = 60

    # -- additional free discovery sources (global) --------------------------------------
    # Pakistan-specific registries investigated and NOT implemented (see docs/API_KEYS.md):
    # SECP eServices (403s a plain HTTP client - WAF-protected), PSX listings (JS-rendered
    # behind reCAPTCHA, no static data), LCCI/FPCCI member directories (session/login-gated,
    # same precedent as the existing ICCI note in discovery/chambers.py). None are faked.
    # GLEIF LEI API (keyless, global legal-entity registry).
    enable_gleif_discovery: bool = True
    gleif_max_queries_per_run: int = 50
    # GLEIF Golden Copy: full LEI-CDF bulk dataset, downloaded once via
    # scripts/fetch_bulk_datasets.py, queried locally via DuckDB.
    enable_gleif_golden_copy: bool = False
    gleif_golden_copy_path: Path = Path("data/gleif/golden_copy.parquet")
    # Wikidata SPARQL (keyless; requires a descriptive User-Agent per Wikidata etiquette).
    enable_wikidata_discovery: bool = True
    wikidata_max_queries_per_run: int = 20
    # UK Companies House (free self-serve key; required to enable).
    enable_companies_house: bool = False
    companies_house_api_key: str | None = None
    companies_house_max_companies_per_run: int = 100
    # US SEC EDGAR (keyless; SEC requires a descriptive User-Agent with a contact address).
    enable_edgar_discovery: bool = True
    edgar_max_companies_per_run: int = 100
    edgar_user_agent: str = "GTMLeadEngine/0.1 (contact: set GTM_EDGAR_CONTACT_EMAIL)"
    # People Data Labs Free Company Dataset: investigated and NOT implemented - the "free"
    # dataset is now gated behind a sales-contact form with no transparent direct download
    # (see docs/API_KEYS.md).

    log_level: str = "INFO"
