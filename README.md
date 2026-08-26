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
python3 scrape.py retry-errors      # only transient network/server failures
python3 scrape.py reclean           # re-apply current email quality rules
```

Batch mode across a preset list of ~100 European and Asian cities:

```bash
python3 harvest_all.py              # all cities
python3 harvest_all.py --only PL,CZ,HU
./run_pipeline.sh                   # DE+NL discover → extract → export, unattended

# Optional: override the default country focus
SCRAPER_COUNTRIES=DE,NL ./run_pipeline.sh

# Ireland + Sweden/Denmark/Norway + Malta (serialized behind pipeline.lock)
./run-ie-scandinavia-malta.sh

# CV-aware target run: ATS discovery + wide OSM + deep company scoring
./run-ultra-targets.sh
```

`run_pipeline.sh` uses a process lock, so a second start cannot create a
concurrent scraper run.

## CV-aware matching

The ultra pipeline scores only evidence published by the company. It combines
five tracks from Emin's CV: finance operations, automation/AI, backend/platform,
data/BI, and finance software. It also reads live job titles, careers signals,
English/international signals, and stores the reasons behind every score.

`deep_enrich.py` revisits product, service, careers and contact pages and records
the exact HTTPS page where the selected email appeared. The final campaign CSV
requires a score of at least 35, a qualified status, and that source-page proof.
An audit CSV contains the score, matching tracks, careers URL, job titles and
email source URL for manual review. Nothing is appended to the outreach queue
automatically.

ATS discovery uses public Greenhouse, Ashby, Lever and Recruitee endpoints. Its
company/board catalogue is downloaded from the open-source
[Colophon Group Job Seek](https://github.com/colophon-group/jobseek) dataset at
the commit pinned in `ats_discovery.py`. Job Seek code is MIT-licensed; its data
is CC BY-NC 4.0 and is used here with attribution for this personal,
non-commercial job search.

## Requirements

Python 3.10+ and `requests`. No headless browser, no paid API — runs comfortably on a 1 vCPU VPS.

## Responsible use

This tool reads business contact details that organisations publish on their own websites, plus openly licensed OpenStreetMap data. Use it accordingly: respect each site's terms, keep request rates low, and handle collected data under the applicable privacy rules (GDPR in the EU) — including honouring opt-out requests. It is not intended for bulk unsolicited commercial mail.
