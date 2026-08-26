from __future__ import annotations

import unittest
from unittest.mock import patch

import ats_discovery
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
            }]
        }
        jobs = ats_discovery.fetch_greenhouse({
            "monitor_config": '{"token":"acme"}',
            "board_url": "https://job-boards.greenhouse.io/acme",
        })
        self.assertEqual(jobs[0].title, "Finance Systems Analyst")
        self.assertEqual(jobs[0].locations, ("Dublin, Ireland",))

    @patch("ats_discovery._get_json")
    def test_ashby_adapter_normalizes_secondary_locations(self, get_json):
        get_json.return_value = {
            "jobs": [{
                "title": "Backend Engineer",
                "location": "Oslo, Norway",
                "secondaryLocations": [{"location": "Bergen, Norway"}],
                "descriptionPlain": "Python API platform",
            }]
        }
        jobs = ats_discovery.fetch_ashby({
            "monitor_config": '{"token":"acme"}',
            "board_url": "https://jobs.ashbyhq.com/acme",
        })
        self.assertEqual(jobs[0].locations, ("Oslo, Norway", "Bergen, Norway"))


if __name__ == "__main__":
    unittest.main()
