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


class FrameWindowClassTests(unittest.TestCase):
    def setUp(self):
        self.observed = {"values": {"frameClosed": {"same": False, "cross": False}},
                         "contexts": {"cross": "http://other.test/frame"}}
        self.events = [{"vv8_kind": "g", "pid": 11,
                        "property": {"value": "closed"}, "receiver": {"class": "Window"},
                        "source": {"url": "http://site.test/engine.js"}} for _ in range(2)]

    def test_verifies_class_and_reports_actual_cross_process_evidence(self):
        unknown = {"vv8_kind": "g", "pid": 99, "source": None,
                   "property": {"value": "closed"}}
        report = runtime.check_frame_window_classes(self.events + [unknown], self.observed)
        self.assertFalse(report["cross_process_frame_observed"])
        foreign = {"vv8_kind": "s", "pid": 12, "origin": self.observed["contexts"]["cross"]}
        report = runtime.check_frame_window_classes(self.events + [foreign], self.observed)
        self.assertTrue(report["cross_process_frame_observed"])
        self.assertEqual(report["cross_frame_pids"], [12])

    def test_rejects_generic_proxy_class_for_remote_window(self):
        self.events[-1]["receiver"]["class"] = "Proxy"
        with self.assertRaisesRegex(AssertionError, "Window receiver class"):
            runtime.check_frame_window_classes(self.events, self.observed)

    def test_rejects_missing_reads_and_unexpected_closed_frame(self):
        with self.assertRaisesRegex(AssertionError, "expected 2"):
            runtime.check_frame_window_classes(self.events[:-1], self.observed)
        self.observed["values"]["frameClosed"]["cross"] = True
        with self.assertRaisesRegex(AssertionError, "reported closed"):
            runtime.check_frame_window_classes(self.events, self.observed)


class SameLineSiteTests(unittest.TestCase):
    SOURCE = ('/*🦊*/\n'
              '(window.__vv8_sequence_sites, window.__vv8_sequence_sites);\n'
              '(window.__vv8_two_sites ? window.__vv8_two_sites : 0);\n')

    def events(self):
        result = []
        prefix = 0
        for number, line in enumerate(self.SOURCE.splitlines(keepends=True), 1):
            for name in ("__vv8_sequence_sites", "__vv8_two_sites"):
                token = "window." + name
                start = line.find(token)
                while start >= 0:
                    column = len(line[:start].encode("utf-16-le")) // 2 + 1
                    result.append({"vv8_kind": "g", "property": {"value": name},
                                   "source_offset": prefix + column - 1,
                                   "source_offset_unit": "utf16",
                                   "source": {"line": number, "column": column}})
                    start = line.find(token, start + len(token))
            prefix += len(line.encode("utf-16-le")) // 2
        return result

    def test_accepts_distinct_exact_utf16_expression_positions(self):
        report = runtime.check_same_line_sites(self.events(), self.SOURCE)
        self.assertEqual(report["__vv8_sequence_sites"]["offsets"], [8, 37])
        self.assertEqual(report["__vv8_two_sites"]["offsets"], [68, 93])

    def test_rejects_two_events_with_one_inherited_pc_position(self):
        events = self.events()
        events[-1]["source_offset"] = events[-2]["source_offset"]
        with self.assertRaisesRegex(AssertionError, "offsets differ"):
            runtime.check_same_line_sites(events, self.SOURCE)


    def test_rejects_distinct_positions_that_do_not_point_to_the_expressions(self):
        events = self.events()
        events[-1]["source_offset"] += 1
        with self.assertRaisesRegex(AssertionError, "offsets differ"):
            runtime.check_same_line_sites(events, self.SOURCE)


class CrossCompartmentGateTests(unittest.TestCase):
    def events(self):
        events = []
        for role, count, operations in (("foreign", 2, ("g", "s")), ("control", 1, ("g",))):
            for suffix, class_name in (("window", "Window"), ("navigator", "Navigator"),
                                       ("element", "HTMLBodyElement")):
                for operation in operations:
                    for _ in range(count if operation == "g" else 1):
                        events.append({"vv8_kind": operation,
                                       "source": {"url": "http://site.test/engine-ccw.js"},
                                       "property": {"value": f"__vv8_ccw_{role}_{suffix}"},
                                       "receiver": {"class": class_name}})
        reference_line = next(index for index, line in enumerate(runtime.fixture_source("engine-ccw.js").splitlines(), 1)
                              if "const foreignReference =" in line)
        events.append({"vv8_kind": "g", "source": {"url": "http://site.test/engine-ccw.js", "line": reference_line},
                       "property": {"value": "getItem"}, "receiver": {"class": "Storage"}})
        for _ in range(2):
            events.append({"vv8_kind": "c", "source": {"url": "http://site.test/engine-ccw.js"},
                           "vv8_fields": ["0", "%getItem"], "receiver": {"class": "Storage"},
                           "args": [{"value": "engine-ccw-native-key"}]})
        return events

    def observed(self):
        return {"values": {"ccw": {"proxyTrapCount": 1}}}

    def test_accepts_foreign_hosts_and_exact_native_call_counts(self):
        report = runtime.check_cross_compartment(self.events(), self.observed())
        self.assertEqual(report["native_calls"], 2)
        self.assertEqual(report["foreign_window_g"], 2)

    def test_rejects_missing_ccw_property_read(self):
        with self.assertRaisesRegex(AssertionError, "expected 2, observed 1"):
            runtime.check_cross_compartment(self.events()[1:], self.observed())

    def test_rejects_proxy_class_on_borrowed_native_receiver(self):
        events = self.events()
        events[-1]["receiver"] = {"class": "Proxy"}
        with self.assertRaisesRegex(AssertionError, "Storage receiver class"):
            runtime.check_cross_compartment(events, self.observed())

    def test_rejects_extra_scripted_proxy_trap_calls(self):
        observed = self.observed()
        observed["values"]["ccw"]["proxyTrapCount"] = 2
        with self.assertRaisesRegex(AssertionError, "Proxy trap count"):
            runtime.check_cross_compartment(self.events(), observed)
