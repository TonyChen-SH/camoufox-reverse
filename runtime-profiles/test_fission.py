import copy
import unittest

from fission import CHECKS, verify_report


class FissionEvidenceTests(unittest.TestCase):
    def report(self):
        checks = {name: {"status": "passed"} for name in CHECKS}
        checks["contexts_and_threads"]["details"] = {"frame_window_classes": {
            "reads": 2, "receiver_classes": ["Window", "Window"],
            "main_pids": [100], "cross_frame_pids": [200],
            "cross_process_frame_observed": True}}
        return {"status": "passed", "checks": checks}

    def test_accepts_complete_gate_with_distinct_native_processes(self):
        self.assertEqual(verify_report(self.report())["cross_frame_pids"], [200])

    def test_rejects_an_incomplete_or_failed_engine_gate(self):
        for name in CHECKS:
            report = self.report()
            report["checks"][name]["status"] = "not_run"
            with self.assertRaisesRegex(ValueError, "complete engine gate"):
                verify_report(report)

    def test_rejects_missing_shared_or_invalid_process_identity(self):
        original = self.report()
        for changed in ({"cross_process_frame_observed": False}, {"cross_frame_pids": []},
                        {"cross_frame_pids": [100]}, {"cross_frame_pids": [100, 200]},
                        {"cross_frame_pids": [True]}, {"main_pids": [0]}):
            report = copy.deepcopy(original)
            report["checks"]["contexts_and_threads"]["details"]["frame_window_classes"].update(changed)
            with self.assertRaisesRegex(ValueError, "different native process"):
                verify_report(report)

    def test_rejects_generic_proxy_class_or_missing_window_read(self):
        for changed in ({"reads": 1}, {"receiver_classes": ["Window", "Proxy"]}):
            report = self.report()
            report["checks"]["contexts_and_threads"]["details"]["frame_window_classes"].update(changed)
            with self.assertRaisesRegex(ValueError, "native Window"):
                verify_report(report)


if __name__ == "__main__":
    unittest.main()
