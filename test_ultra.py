from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import ats_discovery
import deep_enrich
from profile_fit import role_fit, score_profile, target_country_from_location


class ProfileFitTests(unittest.TestCase):
    def test_hybrid_finance_automation_company_qualifies(self):
        result = score_profile(
            "Our international team builds Python APIs and workflow automation "
            "for accounting, payments, financial reporting and Power BI analytics. "
            "Visit our careers page and join our team.",
            name="FinanceFlow",
            domain="financeflow.example",
            job_titles=("Junior Automation Engineer",),
        )
        self.assertTrue(result.qualified)
        self.assertGreaterEqual(result.score, 60)
        self.assertIn("finance_ops", result.tracks)
        self.assertIn("automation_ai", result.tracks)

    def test_unrelated_local_business_is_rejected(self):
        result = score_profile(
            "Family restaurant and beauty salon with massage services.",
            name="Local Place",
        )
        self.assertFalse(result.qualified)

    def test_junior_profile_role_outranks_senior_role(self):
        self.assertGreater(role_fit("Junior Data Analyst"), role_fit("Senior Data Analyst"))

    def test_only_approved_major_cities_match(self):
        self.assertEqual(target_country_from_location("Stockholm, Sweden"), ("SE", "stockholm"))
        self.assertIsNone(target_country_from_location("Waterford, Ireland"))

    def test_five_country_ats_locations_match(self):
        self.assertEqual(target_country_from_location("Warsaw, Poland"), ("PL", "warsaw"))
        self.assertEqual(target_country_from_location("Amsterdam, Netherlands"), ("NL", "amsterdam"))
        self.assertEqual(target_country_from_location("Kirchberg, Luxembourg"), ("LU", "kirchberg"))
        self.assertEqual(target_country_from_location("Poland", {"PL"}), ("PL", "countrywide"))

    def test_explicit_turkish_company_evidence_gets_priority_bonus(self):
        base = score_profile("We build Python workflow automation for accounting teams.")
        evidenced = score_profile(
            "We are a Turkish company headquartered in Istanbul. "
            "We build Python workflow automation for accounting teams."
        )
        self.assertEqual(evidenced.score, base.score + 15)
        self.assertIn("turkish_company:+15", evidenced.reasons)
        self.assertIn("turkish-company", evidenced.keywords)

    def test_turkish_company_signal_variants_are_recognised(self):
        result = score_profile(
            "We are a Turkish-owned technology business founded in Istanbul. "
            "Our international team builds finance automation software."
        )
        self.assertIn("turkish_company:+15", result.reasons)

    def test_name_alone_never_implies_turkish_company(self):
        result = score_profile(
            "We build Python workflow automation for accounting teams.",
            name="Yilmaz Technology",
        )
        self.assertNotIn("turkish_company:+8", result.reasons)


class AtsAdapterTests(unittest.TestCase):
    @patch("ats_discovery._get_json")
    def test_greenhouse_adapter_normalizes_jobs(self, get_json):
        get_json.return_value = {
            "jobs": [{
                "title": "Finance Systems Analyst",
                "location": {"name": "Dublin, Ireland"},
                "offices": [],
                "content": "Python automation and reporting",
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/123",
                "first_published": "2026-08-01T10:00:00Z",
            }]
        }
        jobs = ats_discovery.fetch_greenhouse({
            "monitor_config": '{"token":"acme"}',
            "board_url": "https://job-boards.greenhouse.io/acme",
        })
        self.assertEqual(jobs[0].title, "Finance Systems Analyst")
        self.assertEqual(jobs[0].locations, ("Dublin, Ireland",))
        self.assertEqual(jobs[0].url, "https://job-boards.greenhouse.io/acme/jobs/123")
        self.assertEqual(jobs[0].published_at, "2026-08-01T10:00:00Z")

    @patch("ats_discovery._get_json")
    def test_ashby_adapter_normalizes_secondary_locations(self, get_json):
        get_json.return_value = {
            "jobs": [{
                "title": "Backend Engineer",
                "location": "Oslo, Norway",
                "secondaryLocations": [{"location": "Bergen, Norway"}],
                "descriptionPlain": "Python API platform",
                "jobUrl": "https://jobs.ashbyhq.com/acme/job-id",
                "publishedAt": "2026-08-02T10:00:00Z",
            }]
        }
        jobs = ats_discovery.fetch_ashby({
            "monitor_config": '{"token":"acme"}',
            "board_url": "https://jobs.ashbyhq.com/acme",
        })
        self.assertEqual(jobs[0].locations, ("Oslo, Norway", "Bergen, Norway"))
        self.assertEqual(jobs[0].url, "https://jobs.ashbyhq.com/acme/job-id")

    def test_job_url_rejects_unrelated_or_insecure_hosts(self):
        self.assertEqual(ats_discovery._public_job_url("http://jobs.lever.co/acme/1"), "")
        self.assertEqual(ats_discovery._public_job_url("https://evil.example/acme/1"), "")

    def test_low_visibility_proxy_prefers_small_older_niche_board(self):
        published = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        niche = ats_discovery.Job(
            "Junior Finance Systems Analyst", ("Dublin",), "", "https://jobs.lever.co/acme/1",
            published,
        )
        generic = ats_discovery.Job("Sales Executive", ("Dublin",), "")
        self.assertGreater(
            ats_discovery.low_visibility_priority(niche, 8),
            ats_discovery.low_visibility_priority(generic, 200),
        )

    @patch("ats_discovery._get_json")
    def test_lever_adapter_keeps_canonical_posting_and_date(self, get_json):
        get_json.return_value = [{
            "text": "Automation Specialist",
            "categories": {"location": "Dublin, Ireland"},
            "descriptionPlain": "Python workflows",
            "hostedUrl": "https://jobs.lever.co/acme/job-id",
            "createdAt": 1_700_000_000_000,
        }]
        jobs = ats_discovery.fetch_lever({
            "monitor_config": '{"token":"acme"}',
            "board_url": "https://jobs.lever.co/acme",
        })
        self.assertEqual(jobs[0].url, "https://jobs.lever.co/acme/job-id")
        self.assertTrue(jobs[0].published_at.startswith("2023-11-14"))

    @patch("ats_discovery._get_json")
    def test_recruitee_adapter_keeps_canonical_posting(self, get_json):
        get_json.return_value = {"offers": [{
            "title": "Data Analyst",
            "location": "Dublin, Ireland",
            "description": "Reporting automation",
            "careers_url": "https://acme.recruitee.com/o/data-analyst",
            "published_at": "2026-08-03 10:00:00 UTC",
        }]}
        jobs = ats_discovery.fetch_recruitee({
            "monitor_config": '{}',
            "board_url": "https://acme.recruitee.com/",
        })
        self.assertEqual(jobs[0].url, "https://acme.recruitee.com/o/data-analyst")

    def test_deep_crawler_only_accepts_supported_https_job_seeds(self):
        value = " | ".join((
            "https://jobs.lever.co/acme/1",
            "http://jobs.lever.co/acme/2",
            "https://evil.example/jobs/3",
            "https://jobs.ashbyhq.com/acme/4",
        ))
        self.assertEqual(deep_enrich._ats_seed_urls(value), [
            "https://jobs.lever.co/acme/1",
            "https://jobs.ashbyhq.com/acme/4",
        ])

    @patch("deep_enrich.time.sleep")
    @patch("deep_enrich._page_links")
    @patch("deep_enrich._fetch")
    @patch("deep_enrich._root_page")
    def test_complete_evidence_stops_crawl_after_four_pages(
            self, root_page, fetch, page_links, _sleep):
        html = (
            "<html><body>Python workflow and process automation for accounting, "
            "accounts payable and financial reporting. Our data analytics team "
            "uses SQL and Power BI. We are hiring a Finance Systems Analyst. "
            "<a href='mailto:careers@acme.com'>Apply</a></body></html>"
        )
        root_page.return_value = SimpleNamespace(url="https://acme.com/", text=html)
        fetch.side_effect = lambda _session, url: SimpleNamespace(url=url, text=html)
        page_links.return_value = [
            (10 - index, f"https://acme.com/page-{index}") for index in range(7)
        ]
        pages, _, _ = deep_enrich.crawl_company(
            "acme.com", 8, company_name="Acme", min_score=35,
        )
        self.assertEqual(len(pages), 4)


if __name__ == "__main__":
    unittest.main()


class TurkishServiceSignalTests(unittest.TestCase):
    def test_turkish_speaking_service_page_is_a_turkish_company_signal(self):
        text = ("Administratiekantoor Yildiz. Boekhouding, jaarrekening en belastingaangifte. "
                "Turks sprekend team, Türkçe hizmet veriyoruz. Muhasebe ve vergi danışmanlığı.")
        fit = score_profile(text, name="Administratiekantoor Yildiz", domain="yildiz-administratie.nl")
        self.assertTrue(any(r.startswith("turkish_company:") for r in fit.reasons), fit.reasons)
        self.assertIn("turkish-company", fit.keywords)

    def test_plain_dutch_office_gets_no_turkish_signal(self):
        fit = score_profile("Administratiekantoor De Vries. Boekhouding en belastingaangifte voor het MKB.",
                            name="De Vries", domain="devries.nl")
        self.assertFalse(any(r.startswith("turkish_company:") for r in fit.reasons), fit.reasons)

