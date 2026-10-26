"""HTTP client for the EOSL.ai lifecycle dataset. Stdlib only — no third-party dependencies.

ONE FETCH, NOT N. https://eosl.ai/data/lookup.json carries every tracked part number in a single
document (~93 KB gzipped), so auditing an inventory of any size is one round trip.
"""

import gzip
import io
import json
import urllib.error
import urllib.request

from . import matching

DEFAULT_API_URL = "https://eosl.ai/data/lookup.json"
USER_AGENT = "snipeit-eosl (+https://eosl.ai/)"


class EoslError(Exception):
    """Raised for any failure to obtain or parse the lifecycle data."""


def fetch_lookup(api_url=DEFAULT_API_URL, timeout=30):
    """Fetch and parse the lifecycle lookup document. Returns the list of sku records."""
    req = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise EoslError("HTTP %s fetching %s" % (e.code, api_url)) from e
    except urllib.error.URLError as e:
        raise EoslError("could not reach %s: %s" % (api_url, e.reason)) from e
    except Exception as e:  # noqa: BLE001 - surface the cause, never proceed on partial data
        raise EoslError("failed fetching %s: %s" % (api_url, e)) from e

    # SNIFF THE BYTES, DO NOT TRUST Content-Encoding. Some clients decompress transparently but leave
    # the header on, so branching on the header gunzips already-plain JSON. The magic number
    # describes what the bytes ARE, which is the only thing that matters. (This exact bug shipped in
    # ansible-eosl and was caught only in CI.)
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()

    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise EoslError("response from %s was not valid JSON: %s" % (api_url, e)) from e

    skus = data.get("skus") if isinstance(data, dict) else None
    if not isinstance(skus, list) or not skus:
        # A 200 with an empty or truncated body is the exact failure that silently deleted 124
        # families from this database in July 2026. Treat it as an error, NEVER as "no results" —
        # otherwise every model in the inventory reports as untracked and the run looks clean.
        raise EoslError(
            "response from %s carried no part numbers — refusing to report an empty payload "
            "as 'nothing tracked'" % api_url
        )
    return skus


def fetch_aliases(api_url=DEFAULT_API_URL, timeout=30):
    """Fetch https://eosl.ai/data/aliases.json (variant aliases, HISTORY §82), or None.

    The URL is the lookup URL with lookup.json replaced by aliases.json. Optional by design: any
    failure returns None and matching proceeds exactly as it did before the table existed.
    """
    url = api_url.replace("lookup.json", "aliases.json")
    if url == api_url:
        return None
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        data = json.loads(raw.decode("utf-8"))
        return {"open": data.get("open") or {}, "gated": data.get("gated") or {}}
    except Exception:  # noqa: BLE001 - optional data; never block a run on it
        return None


def resolve_many(parts, vendor_of=None, api_url=DEFAULT_API_URL, timeout=30, skus=None, aliases=None):
    """Resolve an iterable of part numbers to normalised records.

    `vendor_of` is an optional callable part -> vendor name, used only to gate vendor-specific SKU
    aliases. Returns {part_as_supplied: record_or_None}.
    """
    if skus is None:
        skus = fetch_lookup(api_url=api_url, timeout=timeout)
        if aliases is None:
            aliases = fetch_aliases(api_url=api_url, timeout=timeout)
    exact, loose_index = matching.build_index(skus)
    out = {}
    for p in parts:
        if p is None or str(p).strip() == "":
            continue
        key = str(p)
        vendor = vendor_of(key) if vendor_of else None
        out[key] = matching.normalise(matching.match(key, vendor, exact, loose_index, aliases))
    return out
