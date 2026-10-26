"""snipeit-eosl — audit a Snipe-IT inventory against the EOSL.ai lifecycle database.

Read-only. Exit codes: 0 nothing past EOSL, 1 something past EOSL (so it is usable in CI),
2 an error occurred. The distinction matters: "the check failed to run" must never look like
"the check passed".
"""

import argparse
import csv
import json
import os
import sys

from . import eosl, report
from .snipeit import SnipeIt, SnipeItError

__version__ = "1.1.0"


def build_parser():
    p = argparse.ArgumentParser(
        prog="snipeit-eosl",
        description="Audit a Snipe-IT inventory against the EOSL.ai hardware lifecycle database.",
        epilog="Credentials may also come from SNIPEIT_URL and SNIPEIT_TOKEN.",
    )
    p.add_argument("--url", default=os.environ.get("SNIPEIT_URL"),
                   help="Snipe-IT base URL, e.g. https://snipe.example.com")
    p.add_argument("--token", default=os.environ.get("SNIPEIT_TOKEN"),
                   help="Snipe-IT API token (prefer the SNIPEIT_TOKEN env var)")
    p.add_argument("--format", choices=("text", "json", "csv"), default="text")
    p.add_argument("--all", action="store_true",
                   help="include current and untracked models in the output")
    p.add_argument("--output", "-o", help="write to a file instead of stdout")
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--api-url", default=eosl.DEFAULT_API_URL,
                   help="override the EOSL.ai lookup URL (for a mirror or an air-gapped copy)")
    p.add_argument("--insecure", action="store_true",
                   help="skip TLS verification for Snipe-IT (self-signed internal certs only)")
    p.add_argument("--fail-on", choices=("never", "unsupported"), default="unsupported",
                   help="exit 1 when models past end of service life are found (default) or never")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.url or not args.token:
        print("error: --url and --token are required (or set SNIPEIT_URL / SNIPEIT_TOKEN)",
              file=sys.stderr)
        return 2

    try:
        client = SnipeIt(args.url, args.token, timeout=args.timeout, insecure=args.insecure)
        models = client.models()
        skus = eosl.fetch_lookup(api_url=args.api_url, timeout=args.timeout)
    except (SnipeItError, eosl.EoslError) as e:
        # Exit 2, never 0. An audit that could not run has not found "no problems".
        print("error: %s" % e, file=sys.stderr)
        return 2

    if not models:
        print("error: Snipe-IT returned no models — nothing to audit. This is reported as an error "
              "rather than a clean result on purpose.", file=sys.stderr)
        return 2

    aliases = eosl.fetch_aliases(api_url=args.api_url, timeout=args.timeout)
    rep = report.build(models, skus, aliases=aliases)

    if args.format == "json":
        text = json.dumps(rep, indent=2, sort_keys=True)
    elif args.format == "csv":
        import io
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(report.to_csv_rows(rep))
        text = buf.getvalue()
    else:
        text = report.to_text(rep, show_all=args.all)

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")
        print("wrote %s" % args.output, file=sys.stderr)
    else:
        print(text)

    if args.fail_on == "unsupported" and rep["summary"]["unsupported"]["models"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
