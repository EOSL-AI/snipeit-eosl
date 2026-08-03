"""Read-only Snipe-IT API client. Stdlib only.

WHY MODELS AND NOT ASSETS. Snipe-IT stores the part number on the *model* (`model_number`), and every
asset points at one. A 4,000-device estate is typically 40-200 models, so walking `/api/v1/models`
answers "what do I own that is end-of-life" in a handful of requests instead of paging thousands of
assets to read the same field over and over. `assets_count` on each model gives the unit counts for
free. If you need per-asset detail, `--assets` widens the walk deliberately.

THIS CLIENT NEVER WRITES. Snipe-IT is a system of record and a tool that silently edits one is a tool
nobody should run against production. Write-back to a custom field was considered and deliberately
left out — see README, "What this does not do".
"""

import json
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "snipeit-eosl (+https://eosl.ai/)"
PAGE = 100
# A real Snipe-IT can hold a lot of models; this is a runaway-loop backstop, not a coverage limit.
MAX_PAGES = 500


class SnipeItError(Exception):
    """Raised for any failure to obtain or parse data from Snipe-IT."""


class SnipeIt:
    def __init__(self, base_url, token, timeout=30, insecure=False):
        if not base_url:
            raise SnipeItError("a Snipe-IT base URL is required")
        if not token:
            raise SnipeItError("a Snipe-IT API token is required")
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.insecure = insecure

    def _get(self, path, params=None):
        url = "%s/api/v1/%s" % (self.base, path.lstrip("/"))
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": "Bearer %s" % self.token,
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        ctx = None
        if self.insecure:
            import ssl

            ctx = ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            hint = ""
            if e.code in (401, 403):
                hint = " — check the API token and that it has read access"
            raise SnipeItError("HTTP %s from %s%s" % (e.code, url, hint)) from e
        except urllib.error.URLError as e:
            raise SnipeItError("could not reach %s: %s" % (url, e.reason)) from e

        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise SnipeItError("response from %s was not valid JSON: %s" % (url, e)) from e

        # Snipe-IT answers some auth and validation failures with HTTP 200 and a body of
        # {"status":"error","messages":...}. Treating that as an empty result set is how a broken
        # token turns into a clean-looking "no models found" report.
        if isinstance(data, dict) and data.get("status") == "error":
            raise SnipeItError("Snipe-IT returned an error for %s: %s" % (url, data.get("messages")))
        return data

    def _paged(self, path, params=None):
        """Walk a paginated Snipe-IT collection, yielding rows."""
        offset, seen, total = 0, 0, None
        for _ in range(MAX_PAGES):
            p = dict(params or {})
            p.update({"limit": PAGE, "offset": offset})
            data = self._get(path, p)
            rows = data.get("rows")
            if rows is None:
                raise SnipeItError("unexpected response shape from %s: no 'rows'" % path)
            if total is None:
                total = data.get("total")
            for r in rows:
                yield r
            seen += len(rows)
            if not rows or (total is not None and seen >= total):
                break
            # ADVANCE BY WHAT WAS RETURNED, NOT BY PAGE. Snipe-IT clamps `limit` server-side to its
            # `max_results` setting (config/app.php, default 500 but admin-configurable via
            # MAX_RESULTS). On an instance capped below PAGE, `offset += PAGE` would step straight
            # over the rows the server withheld. Found by reading AssetModelsController rather than
            # by testing, because the mock in the test suite answered with whatever it was told to.
            offset += len(rows)
        else:
            raise SnipeItError("pagination did not terminate after %d pages of %s" % (MAX_PAGES, path))

        # A truncated walk that reports success is the IBM failure in a different costume: every
        # model we never saw would silently read as "not in your inventory".
        if total is not None and seen < total:
            raise SnipeItError(
                "only retrieved %d of %d rows from %s — refusing to report a partial inventory"
                % (seen, total, path)
            )

    def models(self):
        """Return [{id, name, model_number, manufacturer, category, assets_count}]."""
        out = []
        for r in self._paged("models"):
            man = r.get("manufacturer") or {}
            cat = r.get("category") or {}
            out.append(
                {
                    "id": r.get("id"),
                    "name": (r.get("name") or "").strip(),
                    "model_number": (r.get("model_number") or "").strip(),
                    "manufacturer": (man.get("name") or "").strip() if isinstance(man, dict) else "",
                    "category": (cat.get("name") or "").strip() if isinstance(cat, dict) else "",
                    "assets_count": r.get("assets_count") or 0,
                }
            )
        return out
