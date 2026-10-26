"""Pure part-number matching and record shaping — no Snipe-IT or network imports.

THIS IS A DELIBERATE PORT of netbox-eosl/netbox_eosl/matching.py and
ansible-eosl/plugins/module_utils/eosl_match.py. Three integrations resolving the same part number
differently would be one fact with three definitions — the failure mode that produced the 844-vs-845
homepage bug. If you change matching here, change it in both of the others.

That instruction has been given twice before and is only as good as the person reading it, so it is
now ENFORCED: `pipeline/matcher-parity-check.py` runs one battery of inputs through all three copies
and fails CI if any of them disagrees. Do not delete that check to make a change land.

Matching is deterministic and conservative: exact, then a punctuation/case-insensitive "loose" match,
then a *vendor-gated* SKU-abbreviation expansion. Never fuzzy, never substring — 'WS-C3850' must not
resolve to 'WS-C3850-24T-L', and an unknown part returns None rather than a near neighbour. A wrong
end-of-life date on the wrong device would defeat the point of a source-backed tool.
"""

import datetime
import re

# Official Fortinet SKU-abbreviation -> product-line prefixes. EOSL.ai stores the long form
# ("FortiGate-60E"); inventories commonly carry the short SKU form ("FG-60E").
FORTINET_PREFIX = {
    "FG": "FORTIGATE",
    "FS": "FORTISWITCH",
    "FAP": "FORTIAP",
    "FAZ": "FORTIANALYZER",
    "FMG": "FORTIMANAGER",
    "FWB": "FORTIWEB",
    "FAD": "FORTIADC",
    "FML": "FORTIMAIL",
    "FPX": "FORTIPROXY",
    "FEX": "FORTIEXTENDER",
    "FAC": "FORTIAUTHENTICATOR",
    "FDD": "FORTIDDOS",
    "FSA": "FORTISANDBOX",
}

BASE_URL = "https://eosl.ai"


def loose(s):
    """Uppercase, strip every non-alphanumeric char. 'FortiGate-60E' -> 'FORTIGATE60E'."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def build_index(skus):
    """Return (exact, loose_index): UPPER(pid)->rec and loose(pid)->rec.

    A loose key shared by two different families is struck out rather than resolved: stripping
    punctuation collapses distinct products onto one key (UAP-Outdoor / UAP-Outdoor+, and Palo Alto
    GP-100 against Dell GP100, across vendors), and first-writer-wins handed back whichever came first
    in the file. Unresolved is the correct answer to an ambiguous key. Same rule as src/match.js and
    functions/mcp.js (HISTORY §82).
    """
    exact, loose_index, ambiguous = {}, {}, set()
    for rec in skus:
        pid = (rec.get("p") or "").strip().upper()
        if not pid:
            continue
        exact.setdefault(pid, rec)
        lk = loose(pid)
        if lk in loose_index:
            if str(loose_index[lk].get("f")) != str(rec.get("f")):
                ambiguous.add(lk)
        else:
            loose_index[lk] = rec
    for k in ambiguous:
        loose_index.pop(k, None)
    return exact, loose_index


def _vendor_ok(vendor, rec):
    """True when no vendor is named, or the named vendor and the record's vendor contain one another."""
    lv, rv = loose(vendor), loose(rec.get("v"))
    return not lv or lv in rv or rv in lv


def _is_fortinet(vendor):
    return bool(vendor) and "FORTINET" in vendor.upper()


def candidate_loose_keys(part, vendor=None):
    """Loose keys to try, most-specific first. Vendor-gated aliases only; deterministic, never fuzzy."""
    keys, seen = [], set()

    def add(raw):
        k = loose(raw)
        if k and k not in seen:
            seen.add(k)
            keys.append(k)

    add(part)
    if _is_fortinet(vendor):
        lp = loose(part)
        for ab, full in FORTINET_PREFIX.items():
            if lp.startswith(ab) and len(lp) > len(ab) and not lp.startswith(full):
                add(full + lp[len(ab):])
    return keys


def match(part, vendor, exact, loose_index, aliases=None):
    """Resolve a part number to a record, or None. Exact -> loose -> Fortinet short code -> variant alias.

    `aliases` is https://eosl.ai/data/aliases.json (optional). Its entries are vetted when the site is
    built: every record an alias could mean has the same vendor and the same dates (EX4300-48T-TAA ->
    EX4300-48T, ASA5506 -> ASA5506-K8). "gated" aliases apply only when a vendor is named, and an alias
    hit must belong to the named vendor, so a Juniper MX80 can never resolve to a Meraki MX80.
    """
    if not part:
        return None
    u = part.strip().upper()
    if u in exact:
        return exact[u]
    keys = candidate_loose_keys(part, vendor)
    for k in keys:
        rec = loose_index.get(k)
        if rec:
            return rec
    if aliases:
        opened, gated = aliases.get("open") or {}, aliases.get("gated") or {}
        for k in keys:
            p = opened.get(k) or (gated.get(k) if loose(vendor) else None)
            rec = exact.get(str(p).upper()) if p else None
            if rec and _vendor_ok(vendor, rec):
                return rec
    return None


def normalise(rec):
    """Expand a compact lookup record into documented, stable field names.

    The wire format is abbreviated to keep the single-fetch payload small; users should never have to
    know that 'xb' means the support-runway band.
    """
    if rec is None:
        return None
    slug = rec.get("s")
    out = {
        "part_number": rec.get("p"),
        "family": rec.get("f"),
        "vendor": rec.get("v"),
        "slug": slug,
        "end_of_sale": rec.get("eos") or None,
        "end_of_service_life": rec.get("eol") or None,
        "active": bool(rec.get("a")),
        "support_runway_score": rec.get("xs"),
        "support_runway_band": rec.get("xb"),
        "source_url": rec.get("src") or None,
        "cisa_bod_26_02_scope": bool(rec.get("bd")),
        "url": "%s/system/%s/" % (BASE_URL, slug) if slug else None,
    }
    out["status"] = status_of(out)
    return out


def status_of(item, today=None):
    """'active' | 'unsupported' | 'supported' | 'unknown' — from the dates alone, never guessed.

    'active' means the manufacturer has announced no end of life. 'unknown' means this database holds
    no end-of-service-life date for the part, which is NOT the same as "still supported" and must
    never be rendered as though it were.
    """
    if item is None:
        return "unknown"
    if item.get("active"):
        return "active"
    eol = item.get("end_of_service_life")
    if not eol:
        return "unknown"
    today = today or datetime.date.today()
    # Dates are 'YYYY-MM' precision in the lookup payload; compare on that precision only rather
    # than inventing a day-of-month.
    return "unsupported" if eol <= today.strftime("%Y-%m") else "supported"
