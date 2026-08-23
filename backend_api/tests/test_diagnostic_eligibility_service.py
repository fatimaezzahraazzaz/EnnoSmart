import unittest

from services.diagnostic_eligibility_service import (
    extract_diagnostic_eligibility_score,
)


class DiagnosticEligibilityScoreTests(unittest.TestCase):
    def test_reads_canonical_report_score(self):
        payload = {
            "report": {
                "frascati_summary": {
                    "rnd_defensibility_index": 0.7,
                    "average_frascati_score": 0.9,
                }
            }
        }
        self.assertEqual(extract_diagnostic_eligibility_score(payload), 0.7)

    def test_supports_historical_nested_report_and_percentage(self):
        payload = {
            "script_or_pipeline_result": {
                "report": {
                    "frascati_summary": {
                        "eligibility_assessment_score": 70,
                    }
                }
            }
        }
        self.assertEqual(extract_diagnostic_eligibility_score(payload), 0.7)

    def test_supports_snapshot_and_zero_score(self):
        payload = {
            "diagnostic_snapshot": {
                "frascati_summary": {"rnd_defensibility_index": 0}
            }
        }
        self.assertEqual(extract_diagnostic_eligibility_score(payload), 0.0)

    def test_falls_back_to_prepare_sources_score(self):
        payload = {
            "prepare_sources_report": {
                "nlp_stats": {"global_frascati_score": 0.64}
            }
        }
        self.assertEqual(extract_diagnostic_eligibility_score(payload), 0.64)

    def test_rejects_missing_or_invalid_values(self):
        self.assertIsNone(extract_diagnostic_eligibility_score({}))
        self.assertIsNone(
            extract_diagnostic_eligibility_score(
                {"frascati_summary": {"rnd_defensibility_index": "inconnu"}}
            )
        )


if __name__ == "__main__":
    unittest.main()
