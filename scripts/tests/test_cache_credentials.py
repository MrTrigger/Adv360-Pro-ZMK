"""Exercise the credential-helper protocol with provisioned and bare credentials."""

import base64
import io
import json
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch


HELPER = Path(__file__).resolve().parents[1] / "bazel/credential-helper"


class CacheCredentialsTest(unittest.TestCase):
    def request(self, credential, uri="https://bazel-cache.wallintech.eu/cas/" + "0" * 64):
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps({"uri": uri}) + "\n")), patch("sys.stdout", output), patch.object(Path, "read_bytes", return_value=credential.encode()):
            runpy.run_path(str(HELPER), run_name="__main__")
        return json.loads(output.getvalue())

    def test_provisioned_curl_config_authenticates_the_account(self):
        for role in ["reader", "writer"]:
            pair = role + "-test:throwaway-password"
            result = self.request('user = "' + pair + '"\n')
            self.assertEqual(result, {"headers": {"Authorization": ["Basic " + base64.b64encode(pair.encode()).decode()]}})

    def test_bare_credentials_remain_supported(self):
        result = self.request("reader-test:throwaway-password\n")
        self.assertEqual(base64.b64decode(result["headers"]["Authorization"][0].split()[1]), b"reader-test:throwaway-password")

    def test_invalid_credentials_are_refused(self):
        for value in ["", "unprefixed:password", "reader-test:", "reader-test:has space", "reader-test:password\nwriter-test:another"]:
            with self.subTest(value=value), self.assertRaises(SystemExit):
                self.request(value)

    def test_credentials_are_confined_to_the_https_cache_origin(self):
        for uri in ["https://other.example/cas/0", "http://bazel-cache.wallintech.eu/cas/0", "https://bazel-cache.wallintech.eu:444/cas/0", "https://user@bazel-cache.wallintech.eu/cas/0"]:
            with self.subTest(uri=uri), self.assertRaises(SystemExit):
                self.request("reader-test:throwaway-password", uri)


if __name__ == "__main__":
    unittest.main()
