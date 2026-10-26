"""Turn Snipe-IT models + EOSL.ai records into an audit report. Pure — no network, no I/O."""

import datetime

from . import matching

# Order matters: this is the order rows are presented and the order a human should act in.
BUCKETS = ["unsupported", "supported", "active", "unknown"]

BUCKET_LABEL = {
    "unsupported": "Past end of service life",
    "supported": "Tracked, still supported",
    # ASCII ONLY in anything printed: this is a CLI, and a Windows cp1252 console renders an
    # em-dash as a replacement character. Guarded by test_text_output_is_ascii.
    "active": "Current - no EOL announced",
    "unknown": "Not tracked by EOSL.ai",
}


def plural(n, word):
    """'1 unit' / '2 units'. Trivial, but wrong grammar in a report reads as a careless tool."""
    return word if n == 1 else word + "s"


def _candidates(model):
    """Part-number candidates for a Snipe-IT model, most authoritative first.

    `model_number` is the field Snipe-IT intends for this. `name` is tried second because a great
    many real Snipe-IT installs leave model_number blank and put "WS-C3850-24T-L" in the name — but
    it is a fallback, and which one matched is reported so the result stays auditable.
    """
    out = []
    for field in ("model_number", "name"):
        v = (model.get(field) or "").strip()
        if v:
            out.append((field, v))
    return out


def build(models, skus, today=None, aliases=None):
    """Return {'rows': [...], 'summary': {...}}.

    One row per Snipe-IT model. `matched_on` records which field resolved, and `status` is computed
    by the shared matcher — never recomputed here.
    """
    today = today or datetime.date.today()
    exact, loose_index = matching.build_index(skus)
    rows = []

    for m in models:
        rec, matched_on, tried = None, None, []
        for field, value in _candidates(m):
            tried.append(value)
            hit = matching.match(value, m.get("manufacturer"), exact, loose_index, aliases)
            if hit is not None:
                rec, matched_on = matching.normalise(hit), field
                break

        status = matching.status_of(rec, today=today) if rec else "unknown"
        rows.append(
            {
                "snipeit_model_id": m.get("id"),
                "snipeit_model": m.get("name"),
                "manufacturer": m.get("manufacturer"),
                "category": m.get("category"),
                "assets": m.get("assets_count") or 0,
                "matched_on": matched_on,
                "queried": tried,
                "status": status,
                "part_number": rec["part_number"] if rec else None,
                "end_of_sale": rec["end_of_sale"] if rec else None,
                "end_of_service_life": rec["end_of_service_life"] if rec else None,
                "support_runway_band": rec["support_runway_band"] if rec else None,
                "source_url": rec["source_url"] if rec else None,
                "url": rec["url"] if rec else None,
            }
        )

    # Sort: worst first, then by unit count — the biggest exposure at the top of the page.
    rows.sort(key=lambda r: (BUCKETS.index(r["status"]), -(r["assets"] or 0), r["snipeit_model"] or ""))

    summary = {b: {"models": 0, "assets": 0} for b in BUCKETS}
    for r in rows:
        s = summary[r["status"]]
        s["models"] += 1
        s["assets"] += r["assets"] or 0
    summary["total_models"] = len(rows)
    summary["total_assets"] = sum(r["assets"] or 0 for r in rows)
    return {"rows": rows, "summary": summary}


def to_text(report, show_all=False):
    """Human-readable report. Returns a string."""
    rows, summary = report["rows"], report["summary"]
    out = []
    out.append("EOSL.ai audit of Snipe-IT inventory")
    out.append("=" * 62)
    for b in BUCKETS:
        s = summary[b]
        if s["models"]:
            out.append("  %-30s %4d %-6s %5d %s" % (BUCKET_LABEL[b], s["models"], plural(s["models"], "model"), s["assets"], plural(s["assets"], "unit")))
    out.append("  %-30s %4d %-6s %5d %s" % ("TOTAL", summary["total_models"], plural(summary["total_models"], "model"), summary["total_assets"], plural(summary["total_assets"], "unit")))
    out.append("")

    shown = rows if show_all else [r for r in rows if r["status"] in ("unsupported", "supported")]
    if not shown:
        out.append("Nothing tracked matched this inventory.")
        # Never let "we matched nothing" read as "you are fine".
        if summary["unknown"]["models"]:
            out.append(
                "NOTE: %d models are not tracked by EOSL.ai. That is not the same as supported - "
                "it means this database holds no date for them." % summary["unknown"]["models"]
            )
        return "\n".join(out)

    for b in BUCKETS:
        sub = [r for r in shown if r["status"] == b]
        if not sub:
            continue
        out.append("%s (%d)" % (BUCKET_LABEL[b].upper(), len(sub)))
        out.append("-" * 62)
        for r in sub:
            out.append(
                "  %-28s %4d unit%s  EOS %-8s EOSL %-8s"
                % (
                    (r["snipeit_model"] or "")[:28],
                    r["assets"],
                    " " if r["assets"] == 1 else "s",
                    r["end_of_sale"] or "-",
                    r["end_of_service_life"] or "-",
                )
            )
            if r["source_url"]:
                out.append("      source: %s" % r["source_url"])
        out.append("")

    if not show_all and summary["unknown"]["models"]:
        out.append(
            "%d model(s) are not tracked by EOSL.ai - not the same as supported. Use --all to list them."
            % summary["unknown"]["models"]
        )
    return "\n".join(out)


def to_csv_rows(report):
    """Return [header, *rows] as lists of strings, for csv.writer."""
    header = [
        "snipeit_model", "manufacturer", "category", "assets", "status",
        "part_number", "matched_on", "end_of_sale", "end_of_service_life",
        "support_runway_band", "source_url", "eosl_ai_url",
    ]
    out = [header]
    for r in report["rows"]:
        out.append([
            r["snipeit_model"] or "", r["manufacturer"] or "", r["category"] or "",
            str(r["assets"]), r["status"], r["part_number"] or "", r["matched_on"] or "",
            r["end_of_sale"] or "", r["end_of_service_life"] or "",
            r["support_runway_band"] or "", r["source_url"] or "", r["url"] or "",
        ])
    return out
