# snipeit-eosl

Audit a [Snipe-IT](https://snipeitapp.com/) inventory against the
[EOSL.ai](https://eosl.ai) hardware end-of-service-life database.

Snipe-IT knows what hardware you own. It does not know when the manufacturer stops supporting it.
This tells you, and links the manufacturer's own bulletin for every date so you can check it.

**Read-only. Zero dependencies. One HTTP request to EOSL.ai regardless of inventory size.**

```
$ snipeit-eosl --url https://snipe.example.com

EOSL.ai audit of Snipe-IT inventory
==============================================================
  Past end of service life          3 models    129 units
  Tracked, still supported          2 models     18 units
  Not tracked by EOSL.ai            3 models     13 units
  TOTAL                             8 models    160 units

PAST END OF SERVICE LIFE (3)
--------------------------------------------------------------
  Catalyst 3850 24-port          40 units  EOS 2020-10 EOSL 2025-10
      source: https://www.cisco.com/c/en/us/products/collateral/...
  MR33-HW                        85 units  EOS 2021-05 EOSL 2026-07
      source: https://documentation.meraki.com/...
```

## Install

```bash
pip install snipeit-eosl
```

## Use

Generate an API token in Snipe-IT under **Account → API Keys**. A read-only user is enough.

```bash
export SNIPEIT_URL=https://snipe.example.com
export SNIPEIT_TOKEN=eyJ0eXAi...

snipeit-eosl                       # human-readable summary
snipeit-eosl --all                 # include current and untracked models
snipeit-eosl --format csv -o eol-audit.csv
snipeit-eosl --format json | jq '.rows[] | select(.status=="unsupported")'
```

**Exit codes:** `0` nothing past end of service life · `1` something is · `2` the audit could not run.
That third one matters: a failed audit must never be mistakable for a clean one. Use it in CI:

```yaml
- run: snipeit-eosl        # fails the job when EOL hardware appears in the inventory
```

## How matching works

Deterministic and conservative, in this order:

1. **Exact** match on the part number.
2. **Loose** match ignoring case and punctuation — `ws c3850 24t l` finds `WS-C3850-24T-L`.
3. **Vendor-gated alias** — `FG-60E` finds `FortiGate-60E`, but *only* when the Snipe-IT manufacturer
   is Fortinet, so one vendor's SKU can never land on another vendor's record.

**It never fuzzy- or substring-matches.** `WS-C3850` does not resolve to `WS-C3850-24T-L`. An unknown
part returns nothing rather than a plausible neighbour, because a confident wrong end-of-life date is
worse than no date.

It reads `model_number` first and falls back to the model `name`, since many real installs leave
`model_number` blank. Which field matched is reported in `matched_on`, so a result is always
traceable.

### Snipe-IT already has an "EOL" field. It does not mean this.

Snipe-IT models carry their own `eol` value, and its API returns it as a string like `"36 months"`.
That is a **depreciation-style estimate of useful life measured from purchase** — a number somebody
at your organisation typed in. It is not a manufacturer date and it has no source behind it.

This tool reports the **manufacturer's published end-of-service-life date**, with a link to the
bulletin it came from. The two answer different questions, and the built-in field is the one people
mistake for this. `snipeit-eosl` reads it never and writes it never.

### "Not tracked" is not "supported"

Models EOSL.ai has no record for are reported as **unknown**, never folded in with the healthy ones.
It means this database holds no date for that part — not that the part is fine. The tooling says so
in every output format on purpose.

## What this does not do

- **It never writes to Snipe-IT.** Snipe-IT is a system of record, and a tool that quietly edits one
  is a tool nobody should point at production. Write-back into a custom field was considered and
  deliberately left out.
- **It does not warrant the dates.** Every record links the manufacturer's bulletin. That bulletin
  governs; this is a convenience layer over it.
- **It does not invent dates.** If a manufacturer has published nothing, nothing is shown.

## Related integrations

Same data, same matching logic, different place to stand:

- **[netbox-eosl](https://github.com/EOSL-AI/netbox-eosl)** — NetBox plugin
- **[ansible-eosl](https://github.com/EOSL-AI/ansible-eosl)** — `eosl.lifecycle` Ansible collection
- **[EOSL.ai API](https://eosl.ai/api/)** — public JSON API, MCP server, and CC BY dataset

## Licence

Apache-2.0. The underlying dataset is **CC BY 4.0** — free for commercial use with attribution.
