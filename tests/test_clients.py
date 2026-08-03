"""Client failure modes. These are the tests that matter most: every one of them is a case where the
wrong behaviour would produce a CLEAN-LOOKING report from broken input.
"""

import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from snipeit_eosl import eosl  # noqa: E402
from snipeit_eosl.snipeit import SnipeIt, SnipeItError  # noqa: E402


def _resp(payload, gzipped=False):
    body = json.dumps(payload).encode()
    if gzipped:
        import gzip
        body = gzip.compress(body)
    m = mock.MagicMock()
    m.read.return_value = body
    m.__enter__.return_value = m
    m.__exit__.return_value = False
    return m


def fake_server(total, cap, cls=None):
    """Run SnipeIt.models() against a fake Snipe-IT that actually honours offset and clamps limit.

    `cap` is the server's max_results. Returns the models the client managed to collect.
    """
    import urllib.parse

    def handler(req, *a, **k):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        offset = int(q.get("offset", ["0"])[0])
        limit = min(int(q.get("limit", ["100"])[0]), cap)      # the server-side clamp
        rows = [{"id": i, "name": "m%d" % i, "model_number": "MN%d" % i,
                 "manufacturer": {"name": "Cisco"}, "category": {"name": "Switch"},
                 "assets_count": 1}
                for i in range(offset, min(offset + limit, total))]
        return _resp({"total": total, "rows": rows})

    with mock.patch("urllib.request.urlopen", side_effect=handler):
        return (cls or SnipeIt)("https://s", "tok").models()


class TestEoslClient(unittest.TestCase):
    def test_parses_normal_payload(self):
        with mock.patch("urllib.request.urlopen", return_value=_resp({"skus": [{"p": "X"}]})):
            self.assertEqual(eosl.fetch_lookup(), [{"p": "X"}])

    def test_gzip_sniffed_by_magic_number_not_header(self):
        # The bug this prevents shipped in ansible-eosl: branching on Content-Encoding gunzips
        # already-decompressed JSON. Both forms must work.
        with mock.patch("urllib.request.urlopen", return_value=_resp({"skus": [{"p": "X"}]}, gzipped=True)):
            self.assertEqual(eosl.fetch_lookup(), [{"p": "X"}])

    def test_empty_sku_list_raises_not_returns_empty(self):
        # THE IBM LESSON. A 200 with a truncated body must never read as "nothing tracked", or every
        # model in the inventory reports as untracked and the run looks clean.
        for payload in ({"skus": []}, {}, {"skus": None}, []):
            with mock.patch("urllib.request.urlopen", return_value=_resp(payload)):
                with self.assertRaises(eosl.EoslError):
                    eosl.fetch_lookup()

    def test_http_error_raises(self):
        err = urllib.error.HTTPError("u", 503, "boom", {}, io.BytesIO(b""))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(eosl.EoslError):
                eosl.fetch_lookup()

    def test_invalid_json_raises(self):
        m = mock.MagicMock()
        m.read.return_value = b"<html>not json</html>"
        m.__enter__.return_value = m
        m.__exit__.return_value = False
        with mock.patch("urllib.request.urlopen", return_value=m):
            with self.assertRaises(eosl.EoslError):
                eosl.fetch_lookup()


class TestSnipeItClient(unittest.TestCase):
    def test_requires_url_and_token(self):
        with self.assertRaises(SnipeItError):
            SnipeIt("", "tok")
        with self.assertRaises(SnipeItError):
            SnipeIt("https://s", "")

    def test_http_200_with_error_body_raises(self):
        # Snipe-IT answers some auth failures with 200 + {"status":"error"}. Treating that as an
        # empty result set turns a bad token into a clean "no models found".
        payload = {"status": "error", "messages": "Unauthorized"}
        with mock.patch("urllib.request.urlopen", return_value=_resp(payload)):
            with self.assertRaises(SnipeItError):
                SnipeIt("https://s", "tok").models()

    def test_partial_pagination_raises(self):
        # total says 250 but the server keeps returning an empty page: refuse rather than report a
        # partial inventory as complete.
        pages = [_resp({"total": 250, "rows": [{"id": i, "name": "m%d" % i} for i in range(100)]}),
                 _resp({"total": 250, "rows": []})]
        with mock.patch("urllib.request.urlopen", side_effect=pages):
            with self.assertRaises(SnipeItError) as cm:
                SnipeIt("https://s", "tok").models()
            self.assertIn("partial inventory", str(cm.exception))

    def test_full_pagination_succeeds_and_flattens(self):
        rows1 = [{"id": i, "name": "m%d" % i, "model_number": "MN%d" % i,
                  "manufacturer": {"name": "Cisco"}, "category": {"name": "Switch"},
                  "assets_count": 2} for i in range(100)]
        rows2 = [{"id": 100, "name": "m100", "model_number": "MN100",
                  "manufacturer": {"name": "Cisco"}, "category": {"name": "Switch"},
                  "assets_count": 1}]
        pages = [_resp({"total": 101, "rows": rows1}), _resp({"total": 101, "rows": rows2})]
        with mock.patch("urllib.request.urlopen", side_effect=pages):
            models = SnipeIt("https://s", "tok").models()
        self.assertEqual(len(models), 101)
        self.assertEqual(models[0]["manufacturer"], "Cisco")
        self.assertEqual(models[-1]["model_number"], "MN100")

    def test_server_clamps_limit_below_our_page_size(self):
        # Snipe-IT clamps `limit` to its max_results setting, which an admin can set below our page
        # size. Advancing by PAGE rather than by len(rows) would step over the withheld rows.
        #
        # THIS NEEDS A FAKE SERVER THAT HONOURS offset, not a fixed list of canned pages. A canned
        # list hands back the next page regardless of the offset requested, so it papers over
        # exactly the arithmetic under test — the first version of this test passed against the
        # broken code too.
        models = fake_server(total=90, cap=40)
        self.assertEqual(len(models), 90)
        self.assertEqual(len({m["id"] for m in models}), 90, "no duplicated or skipped rows")
        self.assertEqual([m["id"] for m in models], list(range(90)), "rows arrive in order, none lost")

    def test_no_clamping_still_works(self):
        # The other half: with no server-side clamp the walk must still be correct, or the test
        # above could pass simply because everything is broken in a compensating way.
        models = fake_server(total=250, cap=1000)
        self.assertEqual([m["id"] for m in models], list(range(250)))

    def test_missing_rows_key_raises(self):
        with mock.patch("urllib.request.urlopen", return_value=_resp({"total": 1})):
            with self.assertRaises(SnipeItError):
                SnipeIt("https://s", "tok").models()

    def test_handles_null_manufacturer_without_crashing(self):
        rows = [{"id": 1, "name": "m", "model_number": "MN", "manufacturer": None,
                 "category": None, "assets_count": None}]
        with mock.patch("urllib.request.urlopen", return_value=_resp({"total": 1, "rows": rows})):
            models = SnipeIt("https://s", "tok").models()
        self.assertEqual(models[0]["manufacturer"], "")
        self.assertEqual(models[0]["assets_count"], 0)


if __name__ == "__main__":
    unittest.main()
