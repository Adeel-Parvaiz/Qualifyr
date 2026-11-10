"""Dead domains must not consume the run's company budget.

The budget is spent before any fetching happens, so a domain that no longer resolves
costs a full slot and returns nothing. The first production run spent 60% of its 120
slots that way while 3854 discovered candidates went untouched.
"""

import asyncio

import pytest

from gtm_engine.models import DiscoveredCompany
from gtm_engine.validation.liveness import host_of


class FakeResolver:
    """Resolves anything except hosts containing 'dead'. Counts lookups."""

    def __init__(self):
        self.seen: list[str] = []

    async def resolves(self, url):
        self.seen.append(url or "")
        return bool(url) and "dead" not in url


def _co(n: int, website: str | None) -> DiscoveredCompany:
    return DiscoveredCompany(name=f"Co {n}", website=website, country="Pakistan", city="Lahore",
                             source="test")


def _take(companies, limit, resolver=None):
    from gtm_engine.pipeline import Pipeline

    pipeline = object.__new__(Pipeline)          # no I/O setup; _take_live only needs .resolver
    pipeline.resolver = resolver or FakeResolver()
    return asyncio.run(Pipeline._take_live(pipeline, companies, limit, None))


@pytest.mark.parametrize("url,expected", [
    ("https://Example.PK/path", "example.pk"),
    ("http://www.foo.com", "www.foo.com"),
    ("example.com", "example.com"),          # some sources emit a bare host
    ("", ""),
])
def test_host_of(url, expected):
    assert host_of(url) == expected


def test_dead_domains_are_replaced_not_merely_dropped():
    # 3 dead ones first: a plain truncation would have yielded 2 live companies.
    companies = [_co(0, "http://dead0.pk"), _co(1, "http://dead1.pk"), _co(2, "http://dead2.pk"),
                 _co(3, "http://live3.pk"), _co(4, "http://live4.pk"),
                 _co(5, "http://live5.pk"), _co(6, "http://live6.pk")]
    picked, dead, leftover = _take(companies, 4)
    assert [c.name for c in picked] == ["Co 3", "Co 4", "Co 5", "Co 6"]
    assert dead == 3
    assert leftover == []


def test_it_stops_resolving_once_the_budget_is_full():
    # The pool is far larger than the budget; resolving all of it would cost more than
    # the dead slots it saves.
    resolver = FakeResolver()
    _take([_co(i, f"http://live{i}.pk") for i in range(500)], 5, resolver)
    assert len(resolver.seen) <= 50, f"resolved {len(resolver.seen)} to fill 5 slots"


def test_unscanned_candidates_are_returned_as_leftover():
    # The 50-wide batch resolves 50 candidates to fill a 5-slot budget; the other 45
    # confirmed-alive ones in that batch, plus the 450 never screened, must still be
    # available for a guaranteed-floor expansion round, not silently dropped.
    companies = [_co(i, f"http://live{i}.pk") for i in range(500)]
    picked, dead, leftover = _take(companies, 5)
    assert len(picked) == 5
    assert dead == 0
    assert len(leftover) == 495
    assert leftover[0].name == "Co 5"   # already-confirmed-alive overflow from the first batch
    assert leftover[45].name == "Co 50"  # first genuinely unscanned candidate


def test_websiteless_companies_fill_the_tail():
    # Nothing to resolve yet, and WebsiteFinder may still find one during processing,
    # so they stay eligible rather than being screened out.
    companies = [_co(0, "http://live0.pk"), _co(1, None), _co(2, None)]
    picked, dead, leftover = _take(companies, 3)
    assert [c.name for c in picked] == ["Co 0", "Co 1", "Co 2"]
    assert dead == 0
    assert leftover == []


def test_all_dead_yields_only_the_websiteless():
    companies = [_co(i, f"http://dead{i}.pk") for i in range(4)] + [_co(4, None)]
    picked, dead, leftover = _take(companies, 2)
    assert [c.name for c in picked] == ["Co 4"]
    assert dead == 4
    assert leftover == []


def test_no_screening_when_candidates_do_not_exceed_the_budget():
    """With nothing queued behind the budget, a dead domain's slot cannot be refilled -
    rejecting it only shrinks the run, and a resolver wrong about one host would cost a
    company for nothing. Also keeps small and offline runs off the network entirely."""
    resolver = FakeResolver()
    companies = [_co(0, "http://dead0.pk"), _co(1, "http://live1.pk")]
    picked, dead, leftover = _take(companies, 5, resolver)
    assert [c.name for c in picked] == ["Co 0", "Co 1"]
    assert dead == 0
    assert leftover == []
    assert resolver.seen == [], "no DNS should happen when screening cannot help"


class _Resolver:
    """Drives HostResolver._lookup by raising whatever a real resolver would."""

    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []
        self.lifetime = 5.0

    async def resolve(self, host, record):
        self.calls.append(record)
        out = self.outcomes.pop(0) if self.outcomes else ["ok"]
        if isinstance(out, Exception):
            raise out
        return out


def _resolves(*outcomes) -> bool:
    import dns.asyncresolver
    from gtm_engine.validation.liveness import HostResolver

    resolver = HostResolver(1.0)
    resolver._resolver = _Resolver(*outcomes)
    return asyncio.run(resolver.resolves("http://example.pk"))


def test_nxdomain_is_dead():
    import dns.resolver

    assert _resolves(dns.resolver.NXDOMAIN()) is False


def test_no_a_or_aaaa_is_dead():
    import dns.resolver

    assert _resolves(dns.resolver.NoAnswer(), dns.resolver.NoAnswer()) is False


def test_aaaa_only_host_is_alive():
    import dns.resolver

    assert _resolves(dns.resolver.NoAnswer(), ["::1"]) is True


def test_a_transient_failure_keeps_the_candidate():
    """Dropping a live site loses a real prospect; keeping a dead one costs one slot
    out of thousands. A timeout says more about our network than about theirs."""
    import dns.exception

    assert _resolves(dns.exception.Timeout(), dns.exception.Timeout()) is True


def test_a_transient_failure_is_retried_before_giving_up():
    import dns.exception

    assert _resolves(dns.exception.Timeout(), ["1.2.3.4"]) is True


def test_no_limit_is_a_passthrough_without_any_dns():
    resolver = FakeResolver()
    companies = [_co(0, "http://dead0.pk"), _co(1, "http://live1.pk")]
    picked, dead, leftover = _take(companies, 0, resolver)
    assert picked == companies and dead == 0
    assert leftover == []
    assert resolver.seen == []
