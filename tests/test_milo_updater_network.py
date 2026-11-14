"""Predictable, safe GitHub Releases updater diagnostics without real network."""
from __future__ import annotations

from io import BytesIO
import json
import socket
import ssl
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

from colibri.milo_core.updates import (
    RELEASES_API, UpdateError, _github_error, _json_https,
    latest_milo_release,
)


class UpdaterConnectionTests(unittest.TestCase):
    def test_release_metadata_selects_latest_actual_published_milo_tag(self):
        data = [
            {"tag_name": "random-v300", "draft": False},
            {"tag_name": "milo-v0.7.0", "draft": False},
            {"tag_name": "milo-v0.8.0", "draft": False},
            {"tag_name": "milo-v0.9.0", "draft": True},
        ]
        with mock.patch("colibri.milo_core.updates.urlopen",
                        return_value=BytesIO(json.dumps(data).encode())) as fetch:
            found = latest_milo_release()
        self.assertEqual(found["tag_name"], "milo-v0.8.0")
        self.assertEqual(fetch.call_args.args[0].full_url, RELEASES_API)

    def test_dns_error_has_actionable_description_after_one_retry(self):
        trouble = URLError(socket.gaierror(-2, "Name resolution failed"))
        with mock.patch("colibri.milo_core.updates.urlopen",
                        side_effect=trouble) as fetch:
            with mock.patch("colibri.milo_core.updates.time.sleep") as wait:
                with self.assertRaisesRegex(UpdateError, "DNS could not resolve"):
                    _json_https(RELEASES_API)
        self.assertEqual(fetch.call_count, 2)
        wait.assert_called_once_with(0.5)

    def test_ssl_error_keeps_certificate_verification_mandatory(self):
        trouble = URLError(ssl.SSLError("CERTIFICATE_VERIFY_FAILED"))
        with mock.patch("colibri.milo_core.updates.urlopen",
                        side_effect=trouble):
            with mock.patch("colibri.milo_core.updates.time.sleep"):
                with self.assertRaisesRegex(UpdateError, "TLS certificate"):
                    _json_https(RELEASES_API)

    def test_rate_limit_and_permanent_not_found_are_distinct(self):
        rate_limited = HTTPError(
            RELEASES_API, 403, "Forbidden", {"X-RateLimit-Remaining": "0"}, None)
        missing = HTTPError(RELEASES_API, 404, "Not found", {}, None)
        self.assertIn("rate limit", _github_error(rate_limited))
        self.assertIn("HTTP 404", _github_error(missing))
        with mock.patch("colibri.milo_core.updates.urlopen",
                        side_effect=missing) as fetch:
            with self.assertRaisesRegex(UpdateError, "HTTP 404"):
                _json_https(RELEASES_API)
        self.assertEqual(fetch.call_count, 1)

    def test_reject_untrusted_host_and_path_before_network(self):
        with mock.patch("colibri.milo_core.updates.urlopen") as fetch:
            for url in (
                "https://evil.example/repos/BoomForge/colibri/releases",
                RELEASES_API + "&other=data",
                "https://api.github.com/repos/BoomForge/colibri/unknown",
            ):
                with self.assertRaisesRegex(UpdateError, "untrusted"):
                    _json_https(url)
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
