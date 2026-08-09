# Lead Scraper

![Company records scattered across Europe are funnelled through filtering stages into a single structured dataset](docs/banner.png)

Multi-country company data pipeline: discovers businesses from OpenStreetMap, crawls each company's own website, and extracts **published** contact emails into a deduplicated dataset.

Built to power an automated job-application outreach system across 20+ countries.

## What it does

```
OpenStreetMap (Overpass API)  ─┐
Directory / listing pages      ├─►  candidate domains  ─►  site crawl  ─►  verified emails  ─►  CSV
Manual seeds                  ─┘        (SQLite)          (19 paths)       (deduplicated)
```

1. **Discover** — queries the Overpass API for `office=accountant|tax_advisor|financial|employment_agency` in any city, or harvests outbound domains from a directory page.
2. **Crawl** — for each domain, tries 19 contact-page conventions (`/contact`, `/kontakt`, `/impressum`, `/iletisim`, …) over HTTPS with HTTP fallback, stopping at the first page that yields an address.
3. **Extract** — regex + **Cloudflare `data-cfemail` decoding**, then filters platform noise (`noreply@`, analytics vendors, site-builder addresses) and keeps only addresses on the company's own domain or a known mailbox provider.
4. **Export** — emits campaign-ready CSV, removing rows that duplicate an existing email *or* an existing organization.

## Design notes

- **No guessing.** Addresses are only ever read from a page the company publishes. The tool never constructs `info@<domain>` patterns.
- **Resumable.** All state lives in SQLite; interrupt at any point and rerun — completed work is never repeated.
- **Identity-safe deduplication.** Companies on shared mailbox providers (Gmail, GMX, …) are keyed by address rather than domain, so two firms using the same provider never collapse into one record.
- **Polite by default.** Small worker pool, per-request delays, descriptive User-Agent with a contact address, honours Overpass rate limits with endpoint failover.
- **Data stays local.** Harvested records are git-ignored; this repository contains the tool, never the dataset.

## Usage

```bash
export SCRAPER_CONTACT="you@example.com"   # required by Overpass usage policy

# 1. discover
python3 scrape.py osm --area Rotterdam --country NL
python3 scrape.py osm --area Hamburg --country DE --types accountant,tax_advisor
python3 scrape.py links --url https://example-directory.com/members --country DE
python3 scrape.py seed --domains firm-a.nl,firm-b.de --country NL

# 2. extract
python3 scrape.py emails --limit 200 --workers 6

# 3. export
python3 scrape.py export --out new-companies.csv

python3 scrape.py stats
```

Batch mode across a preset list of ~100 European and Asian cities:

```bash
python3 harvest_all.py              # all cities
python3 harvest_all.py --only PL,CZ,HU
./run_pipeline.sh                   # discover → extract → export, unattended
```

## Requirements

Python 3.10+ and `requests`. No headless browser, no paid API — runs comfortably on a 1 vCPU VPS.

## Responsible use

This tool reads business contact details that organisations publish on their own websites, plus openly licensed OpenStreetMap data. Use it accordingly: respect each site's terms, keep request rates low, and handle collected data under the applicable privacy rules (GDPR in the EU) — including honouring opt-out requests. It is not intended for bulk unsolicited commercial mail.
