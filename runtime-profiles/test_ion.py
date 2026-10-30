"""Parser/acceptance contracts; these tests do not execute a browser or prove Ion."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("ion_profile", Path(__file__).with_name("ion.py"))
ion = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ion)
URL = "http://127.0.0.1:8123/ion.js"


def native_profile(implementation="ion", url=URL):
    # Pinned ProfiledThreadData.cpp schemas; deliberately reordered to require schema lookup.
    strings = [implementation] + [f"{name} ({url}:12:1)[7]" for name in ion.FUNCTIONS]
    thread = {
        "pid": 42, "tid": 43,
        "samples": {"schema": {"time": 0, "stack": 1}, "data": [[10, 2]]},
        "stackTable": {"schema": {"frame": 0, "prefix": 1},
                       "data": [[0, None], [1, 0], [2, 1]]},
        "frameTable": {"schema": {"implementation": 0, "location": 1},
                       "data": [[0, 1], [0, 2], [0, 3]]},
        "stringTable": strings,
    }
    return {"meta": {"version": 34}, "threads": [thread], "processes": []}


class NativeProfilerTests(unittest.TestCase):
    def test_requires_actual_sample_stack_and_follows_prefix(self):
        result = ion.sampled_ion(native_profile(), URL)
        self.assertEqual({name: details["samples"] for name, details in result.items()},
                         dict.fromkeys(ion.FUNCTIONS, 1))
        self.assertEqual(result["ionRead"]["examples"][0]["frame"], 0)

    def test_accepts_sampled_child_process_and_labels_without_source_index(self):
        child = native_profile()
        child["threads"][0]["stringTable"] = [value.removesuffix("[7]")
                                                     for value in child["threads"][0]["stringTable"]]
        parent = {"threads": [], "processes": [child]}
        self.assertEqual(ion.sampled_ion(parent, URL)["ionCall"]["samples"], 1)

    def test_rejects_unreferenced_ion_frames(self):
        profile = native_profile()
        thread = profile["threads"][0]
        thread["stringTable"].append("baseline")
        thread["frameTable"]["data"].append([4, 1])
        thread["stackTable"]["data"].append([3, None])
        thread["samples"]["data"] = [[10, 3]]
        with self.assertRaisesRegex(AssertionError, "No actual sampled Ion"):
            ion.sampled_ion(profile, URL)

    def test_rejects_baseline_and_interpreter(self):
        for implementation in ("baseline", "blinterp"):
            with self.subTest(implementation=implementation):
                with self.assertRaisesRegex(AssertionError, "No actual sampled Ion"):
                    ion.sampled_ion(native_profile(implementation), URL)

    def test_rejects_other_sites_lookalike_urls_and_other_functions(self):
        for url in ("http://other.invalid/ion.js", URL + "?other=1", URL + ".foreign"):
            with self.subTest(url=url):
                with self.assertRaisesRegex(AssertionError, "No actual sampled Ion"):
                    ion.sampled_ion(native_profile(url=url), URL)
        profile = native_profile()
        profile["threads"][0]["stringTable"][1] = f"fakeionRead ({URL}:12:1)"
        with self.assertRaisesRegex(AssertionError, "ionRead"):
            ion.sampled_ion(profile, URL)

    def test_rejects_empty_samples_and_incomplete_named_function_evidence(self):
        profile = native_profile()
        profile["threads"][0]["samples"]["data"] = []
        with self.assertRaisesRegex(AssertionError, "No actual sampled Ion"):
            ion.sampled_ion(profile, URL)
        profile = native_profile()
        profile["threads"][0]["samples"]["data"] = [[10, 0]]
        with self.assertRaisesRegex(AssertionError, "ionWrite.*ionCall"):
            ion.sampled_ion(profile, URL)

    def test_rejects_corrupt_referenced_stack(self):
        for stack in (99, True):
            with self.subTest(stack=stack):
                profile = native_profile()
                profile["threads"][0]["samples"]["data"] = [[10, stack]]
                with self.assertRaisesRegex(AssertionError, "index"):
                    ion.sampled_ion(profile, URL)
        profile = native_profile()
        profile["threads"][0]["stackTable"]["data"][0][1] = 2
        with self.assertRaisesRegex(AssertionError, "Cyclic"):
            ion.sampled_ion(profile, URL)


def phase_events():
    def event(kind, prop=None, value=None):
        return {"vv8_kind": kind, "source": {"url": URL}, "pid": 42, "tid": 43,
                "property": {"value": prop}, "args": [{"value": value}],
                "vv8_fields": [12, "%getItem"] if kind == "c" else []}

    body = [event("g", "__vv8_ion_read"), event("s", "__vv8_ion_write", 0),
            event("g", "getItem"), event("c", value="engine-ion-native")]
    return ([event("s", "__vv8_ion_phase", "measure-start")]
            + body + copy.deepcopy(body)
            + [event("s", "__vv8_ion_phase", "measure-end")])


class ExactTraceContractTests(unittest.TestCase):
    def test_expected_page_values_use_independent_modular_arithmetic(self):
        with patch.dict(ion.PARAMETERS, {"warmupBatches": 1, "warmupIterations": 3,
                                       "measuredIterations": 5, "spins": 7}):
            values = ion.expected_values()
        for phase, iterations in ((values["warmup"][0], 3), (values["measured"], 5)):
            checksum = 0
            for _ in range(iterations * 7):
                checksum = (checksum * 1664525 + 1013904223) & 0xffffffff
            if checksum >= 0x80000000:
                checksum -= 0x100000000
            self.assertEqual(phase, {"read": [7 * iterations, checksum],
                                     "write": [iterations - 1, checksum],
                                     "call": [9 * iterations, checksum]})

    def test_accepts_exact_counts_between_unique_owned_markers(self):
        events = phase_events()
        unrelated = copy.deepcopy(events[1])
        unrelated["source"]["url"] = "http://other.invalid/ion.js"
        result = ion.trace_counts([unrelated] + events, URL, "measure", 2)
        self.assertEqual(result["observed"], dict.fromkeys(
            ("read", "write", "method_reference", "native_call"), 2))

    def test_rejects_missing_extra_unattributed_and_wrong_thread_events(self):
        cases = [phase_events() for _ in range(4)]
        cases[0].pop(1)
        cases[1].insert(1, copy.deepcopy(cases[1][1]))
        cases[2][1]["source"] = None
        cases[3][1]["tid"] = 99
        for events in cases:
            with self.subTest(events=events):
                with self.assertRaises(AssertionError):
                    ion.trace_counts(events, URL, "measure", 2)

    def test_rejects_missing_or_duplicate_phase_markers(self):
        events = phase_events()
        for changed in (events[1:], [events[0]] + events):
            with self.assertRaisesRegex(AssertionError, "phase marker"):
                ion.trace_counts(changed, URL, "measure", 2)

    def test_missing_binary_fails_and_writes_machine_report(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            self.assertEqual(ion.validate(Path(directory) / "missing-browser", output), 1)
            report = json.loads((output / "ion-report.json").read_text())
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["checks"]["off_capture"]["status"], "failed")
            self.assertEqual(report["checks"]["sampled_ion"]["status"], "not_run")


if __name__ == "__main__":
    unittest.main()
