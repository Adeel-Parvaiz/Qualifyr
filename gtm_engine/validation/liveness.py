"""Does this domain still exist?

Overture's `websites` field ages badly. On the first production run, 60% of the
candidate sites we failed to crawl did not resolve at all - checked from inside
Pakistan, so this is stale data rather than geo-blocking or a crawler fault.

That matters because a run's company budget is spent *before* anything is fetched:
a dead domain occupies one of `max_companies` slots and yields nothing. A DNS lookup
costs milliseconds where a crawl costs seconds, so screening first is close to free.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

import dns.asyncresolver
import dns.exception
import dns.resolver

log = logging.getLogger(__name__)


def host_of(url: str) -> str:
    """The hostname in `url`, tolerating the bare "example.com" that some sources emit."""
    split = urlsplit(url if "//" in url else f"//{url}")
    return (split.hostname or "").strip(".").lower()


class HostResolver:
    """Async A/AAAA lookup with an in-process cache, one domain resolved once per run."""

    def __init__(self, timeout_s: float = 5.0):
        self._resolver = dns.asyncresolver.Resolver()
        self._resolver.lifetime = timeout_s
        self._cache: dict[str, bool] = {}
        self._lock = asyncio.Lock()

    async def resolves(self, url: str | None) -> bool:
        host = host_of(url or "")
        if not host:
            return False
        if host in self._cache:
            return self._cache[host]
        async with self._lock:
            if host in self._cache:
                return self._cache[host]
        alive = await self._lookup(host)
        self._cache[host] = alive
        return alive

    async def _lookup(self, host: str) -> bool:
        """Only a definitive "this name does not exist" counts as dead.

        The two mistakes are not symmetric. Calling a live site dead discards a real
        prospect for good; calling a dead site live costs one crawl slot out of
        thousands of candidates. So a timeout, a SERVFAIL, or an unreachable nameserver
        - all of which say more about our network than theirs - keep the candidate.
        Measured, not assumed: screening 38 already-crawled sites wrongly rejected one
        that resolved fine on retry.
        """
        transient: Exception | None = None
        for _ in range(2):
            try:
                await self._resolver.resolve(host, "A")
                return True
            except dns.resolver.NXDOMAIN:
                return False
            except dns.resolver.NoAnswer:
                # A record absent: an AAAA-only host is still reachable.
                try:
                    await self._resolver.resolve(host, "AAAA")
                    return True
                except dns.resolver.NXDOMAIN:
                    return False
                except dns.resolver.NoAnswer:
                    return False  # neither record: no address to connect to
                except dns.exception.DNSException as exc:
                    transient = exc
            except dns.exception.DNSException as exc:
                transient = exc

        log.debug("liveness: keeping %s despite %s", host, type(transient).__name__)
        return True
