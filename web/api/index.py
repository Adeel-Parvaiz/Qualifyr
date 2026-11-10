"""Vercel Python entrypoint. Wraps the FastAPI app defined in the repo-root gtm_engine
package - no endpoint logic lives here.

Two layouts have to work, and they differ in where gtm_engine sits relative to this file:

  local dev   E:/job/gtm-leads/{gtm_engine,config}  with this file at web/api/index.py
  Vercel      /var/task/{gtm_engine,config}         with this file at api/index.py

On Vercel the Root Directory is web/, so web/ *becomes* the deployment root and
everything above it is gone - the repo root simply does not exist at runtime. That is
why vercel.json's installCommand copies gtm_engine/ and config/ into web/ during the
build (and why web/.gitignore excludes those copies). Rather than hardcode a parent
depth that is right in one layout and wrong in the other, walk up until we find the
package.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
for _root in _HERE.parents:
    if (_root / "gtm_engine").is_dir():
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        break
else:  # pragma: no cover - only reachable if the build step did not run
    raise ModuleNotFoundError(
        f"gtm_engine not found above {_HERE}. On Vercel this means vercel.json's "
        "installCommand did not copy it into the root directory."
    )

from gtm_engine.api.main import app as _app  # noqa: E402


async def app(scope, receive, send):
    """Strip the /api prefix that vercel.json's rewrite adds, so gtm_engine's routes
    (defined as /health, /campaigns, ...) keep matching unmodified.

    The rewrite hands us the *original* request path, so /api/health arrives here as
    /api/health even though vercel.json routed it via /api/index.py.
    """
    if scope["type"] == "http" and scope["path"].startswith("/api"):
        scope = dict(scope)
        scope["path"] = scope["path"][len("/api"):] or "/"
    await _app(scope, receive, send)
