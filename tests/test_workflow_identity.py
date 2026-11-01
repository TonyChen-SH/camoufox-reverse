"""Keep composition receipts and GitHub artifact source identities identical."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WorkflowIdentityTests(unittest.TestCase):
    def test_checksummed_hidden_example_is_included_in_both_browser_deliveries(self):
        self.assertTrue((ROOT / 'examples/.gitattributes').is_file())
        text = (ROOT / '.github/workflows/native-browser.yml').read_text()
        deliveries = []
        for step in re.split(r'^      - ', text, flags=re.MULTILINE):
            if 'uses: actions/upload-artifact@v4' not in step or '          path: dist/\n' not in step:
                continue
            deliveries.append(step)
            self.assertIn('          include-hidden-files: true\n', step)
        self.assertEqual(len(deliveries), 2)

    def test_both_jobs_checkout_the_artifact_head_instead_of_the_pr_merge_commit(self):
        text = (ROOT / ".github/workflows/native-browser.yml").read_text()
        jobs = dict(re.findall(r"^  ([a-zA-Z0-9_-]+):\n(.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
                               text.split("jobs:\n", 1)[1], re.MULTILINE | re.DOTALL))
        for name in ("linux-x86_64", "browser-runtime"):
            with self.subTest(job=name):
                root_checkout = re.search(
                    r"^      - uses: actions/checkout@v4\n        with:\n((?:          [^\n]*\n)+)",
                    jobs[name], re.MULTILINE)
                self.assertIsNotNone(root_checkout)
                options = root_checkout.group(1)
                self.assertNotIn("repository:", options)
                self.assertNotIn("path:", options)
                self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}\n", options)


if __name__ == "__main__":
    unittest.main()
