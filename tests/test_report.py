"""Report shaping and matching behaviour. No network."""

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from snipeit_eosl import matching, report  # noqa: E402

TODAY = datetime.date(2026, 8, 2)

SKUS = [
    {"p": "WS-C3850-24T-L", "f": "Catalyst 3850", "v": "Cisco", "s": "cisco-catalyst-3850",
     "eos": "2020-10", "eol": "2025-10", "xs": 10, "xb": "High exposure",
     "src": "https://cisco.example/3850"},
    {"p": "FortiGate-60E", "f": "FortiGate 60E", "v": "Fortinet", "s": "fortinet-fortigate-60e",
     "eos": "2024-06", "eol": "2029-06", "xs": 60, "xb": "Plan", "src": "https://forti.example/60e"},
    {"p": "C9300-24T", "f": "Catalyst 9300", "v": "Cisco", "s": "cisco-catalyst-9300",
     "a": True, "xs": 100, "xb": "Clear", "src": "https://cisco.example/9300"},
]


def model(name, number="", man="", count=1, cat="Switch", mid=1):
    return {"id": mid, "name": name, "model_number": number, "manufacturer": man,
            "category": cat, "assets_count": count}


class TestMatching(unittest.TestCase):
    def setUp(self):
        self.exact, self.loose = matching.build_index(SKUS)

    def m(self, part, vendor=None):
        return matching.match(part, vendor, self.exact, self.loose)

    def test_exact_and_loose(self):
        self.assertIsNotNone(self.m("WS-C3850-24T-L"))
        self.assertIsNotNone(self.m("ws-c3850-24t-l"))
        self.assertIsNotNone(self.m("WS C3850 24T L"))
        self.assertIsNotNone(self.m("WSC385024TL"))

    def test_never_substring_or_prefix(self):
        # These are the controls. A matcher that matched everything would pass every test above.
        self.assertIsNone(self.m("WS-C3850"), "a prefix of a real part must not match")
        self.assertIsNone(self.m("WS-C3850-24T-L-XYZ"), "a superstring must not match")
        self.assertIsNone(self.m("C3850"))
        self.assertIsNone(self.m("TOTALLY-MADE-UP"))

    def test_vendor_gated_alias(self):
        self.assertIsNotNone(self.m("FG-60E", "Fortinet"))
        # The gate is the point: the same input under another vendor must not cross-match.
        self.assertIsNone(self.m("FG-60E", "Cisco"))
        self.assertIsNone(self.m("FG-60E", None))

    def test_status_from_dates_only(self):
        past = matching.normalise(self.m("WS-C3850-24T-L"))
        future = matching.normalise(self.m("FortiGate-60E"))
        active = matching.normalise(self.m("C9300-24T"))
        self.assertEqual(matching.status_of(past, TODAY), "unsupported")
        self.assertEqual(matching.status_of(future, TODAY), "supported")
        self.assertEqual(matching.status_of(active, TODAY), "active")
        self.assertEqual(matching.status_of(None, TODAY), "unknown")


class TestReport(unittest.TestCase):
    def test_matches_model_number_first_and_records_which(self):
        rep = report.build([model("Some friendly name", "WS-C3850-24T-L", "Cisco", 12)], SKUS, TODAY)
        row = rep["rows"][0]
        self.assertEqual(row["matched_on"], "model_number")
        self.assertEqual(row["status"], "unsupported")
        self.assertEqual(row["assets"], 12)
        self.assertEqual(row["source_url"], "https://cisco.example/3850")

    def test_falls_back_to_name_when_model_number_blank(self):
        rep = report.build([model("WS-C3850-24T-L", "", "Cisco", 3)], SKUS, TODAY)
        self.assertEqual(rep["rows"][0]["matched_on"], "name")
        self.assertEqual(rep["rows"][0]["status"], "unsupported")

    def test_untracked_is_unknown_not_supported(self):
        rep = report.build([model("Dell Latitude 5420", "LAT5420", "Dell", 90)], SKUS, TODAY)
        row = rep["rows"][0]
        self.assertEqual(row["status"], "unknown")
        self.assertIsNone(row["end_of_service_life"])
        self.assertIsNone(row["matched_on"])

    def test_sorted_worst_first_then_by_unit_count(self):
        models = [
            model("current", "C9300-24T", "Cisco", 50, mid=1),
            model("dead-small", "WS-C3850-24T-L", "Cisco", 2, mid=2),
            model("fine", "FortiGate-60E", "Fortinet", 5, mid=3),
        ]
        rep = report.build(models, SKUS, TODAY)
        self.assertEqual([r["status"] for r in rep["rows"]],
                         ["unsupported", "supported", "active"])

    def test_summary_counts_models_and_units(self):
        models = [
            model("a", "WS-C3850-24T-L", "Cisco", 10, mid=1),
            model("b", "WS-C3850-24T-L", "Cisco", 5, mid=2),
            model("c", "nope", "Dell", 7, mid=3),
        ]
        rep = report.build(models, SKUS, TODAY)
        self.assertEqual(rep["summary"]["unsupported"], {"models": 2, "assets": 15})
        self.assertEqual(rep["summary"]["unknown"], {"models": 1, "assets": 7})
        self.assertEqual(rep["summary"]["total_assets"], 22)

    def test_text_output_never_implies_untracked_means_safe(self):
        rep = report.build([model("Mystery box", "ZZZ-1", "Acme", 4)], SKUS, TODAY)
        text = report.to_text(rep)
        self.assertIn("not the same as supported", text)

    def test_text_output_is_ascii(self):
        # A Windows cp1252 console turns an em-dash into a replacement character. Every printed
        # string must survive that, including the bucket labels.
        models = [model("a", "WS-C3850-24T-L", "Cisco", 1, mid=1),
                  model("b", "FortiGate-60E", "Fortinet", 1, mid=2),
                  model("c", "C9300-24T", "Cisco", 1, mid=3),
                  model("d", "ZZZ", "Acme", 1, mid=4)]
        text = report.to_text(report.build(models, SKUS, TODAY), show_all=True)
        self.assertIn("Current - no EOL announced", text)   # the label actually rendered
        try:
            text.encode("ascii")
        except UnicodeEncodeError as e:
            self.fail("non-ASCII in CLI output: %r" % text[e.start:e.end])

    def test_csv_header_and_row_widths_match(self):
        rep = report.build([model("a", "WS-C3850-24T-L", "Cisco", 1)], SKUS, TODAY)
        rows = report.to_csv_rows(rep)
        self.assertTrue(all(len(r) == len(rows[0]) for r in rows))


if __name__ == "__main__":
    unittest.main()
