import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path

from research_pipeline import build_report, init_database, normalize_company, validate_members


def member(company="Northstar Medical", city="Huntsville", industry="medical",
           website="https://northstar.example", description="New location and now hiring"):
    return {"company": company, "city": city, "industry": industry,
            "website": website, "description": description}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = init_database(Path(self.temp.name) / "history.sqlite3")
        self.as_of = dt.date(2026, 8, 13)

    def tearDown(self):
        self.database.close()
        self.temp.cleanup()

    def test_existing_customer_is_never_reported(self):
        report = build_report([member()], {"Northstar Medical"}, self.database, self.as_of)
        self.assertEqual(report["lead_count"], 0)
        self.assertEqual(report["exclusions"]["existing_customer"], 1)

    def test_recent_lead_is_excluded_for_ninety_days(self):
        self.database.execute(
            "INSERT INTO report_history VALUES (?, ?)",
            (normalize_company("Northstar Medical"), "2026-07-01"),
        )
        report = build_report([member()], set(), self.database, self.as_of)
        self.assertEqual(report["lead_count"], 0)
        self.assertEqual(report["exclusions"]["recently_reported"], 1)

    def test_lead_returns_after_ninety_day_window(self):
        self.database.execute(
            "INSERT INTO report_history VALUES (?, ?)",
            (normalize_company("Northstar Medical"), "2026-05-01"),
        )
        report = build_report([member()], set(), self.database, self.as_of)
        self.assertEqual(report["lead_count"], 1)

    def test_outside_territory_and_bad_website_fail_closed(self):
        records = [member(company="Outside", city="Birmingham"),
                   member(company="Bad Link", website="not a website")]
        report = build_report(records, set(), self.database, self.as_of)
        self.assertEqual(report["lead_count"], 0)
        self.assertEqual(report["exclusions"]["outside_territory"], 1)
        self.assertEqual(report["exclusions"]["invalid_website"], 1)

    def test_score_is_ranked_and_explained(self):
        records = [member(company="Stable Office", description="Established clinic"),
                   member(company="Growing Office", description="New location, expanding and now hiring")]
        report = build_report(records, set(), self.database, self.as_of)
        self.assertEqual(report["leads"][0]["company"], "Growing Office")
        self.assertIn("new location", report["leads"][0]["score_reasons"])
        self.assertGreater(report["leads"][0]["score"], report["leads"][1]["score"])

    def test_report_never_claims_outreach_occurred(self):
        report = build_report([member()], set(), self.database, self.as_of)
        self.assertEqual(report["safety"], {"companies_contacted": False, "forms_submitted": False})

    def test_invalid_input_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_members({"company": "not a list"})


if __name__ == "__main__":
    unittest.main()

