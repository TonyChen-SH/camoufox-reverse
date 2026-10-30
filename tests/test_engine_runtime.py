import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("engine_runtime_check", ROOT / "tools/engine_runtime_check.py")
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class NavigationContextTests(unittest.TestCase):
    def setUp(self):
        self.original = "http://127.0.0.1:8123/engine.html?frameOrigin=local"
        self.hrefs = {"push": self.original + "&engineNavigation=push",
                      "hash": self.original + "&engineNavigation=push#engine-context",
                      "restore": self.original}
        self.contexts = {"main": self.original, "navigation": self.hrefs}
        self.events = [{"vv8_kind": "s", "property": {"value": "__vv8_context_" + kind},
                        "origin": href} for kind, href in self.hrefs.items()]

    def test_accepts_current_native_href_after_each_history_operation(self):
        result = runtime.check_context_navigation(self.events, self.contexts)
        self.assertEqual(result, self.hrefs)

    def test_rejects_original_document_uri_after_push_or_hash(self):
        for index in (0, 1):
            with self.subTest(operation=index):
                events = [dict(event) for event in self.events]
                events[index]["origin"] = self.original
                with self.assertRaisesRegex(AssertionError, "href differs"):
                    runtime.check_context_navigation(events, self.contexts)

    def test_requires_one_assignment_for_each_history_operation(self):
        for events in (self.events[:-1], self.events + [self.events[0]]):
            with self.subTest(events=len(events)):
                with self.assertRaisesRegex(AssertionError, "assignment count"):
                    runtime.check_context_navigation(events, self.contexts)
