import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class EngineBufferTests(unittest.TestCase):
    def test_raw_utf16_and_transport_survive_real_cpp_framing(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "engine-buffer-test"
            subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra",
                "-Werror", "-fsanitize=undefined", "-fno-sanitize-recover=all",
                "-I", str(ROOT / "additions/js/src/vm"),
                str(ROOT / "tests/test_engine_buffer.cpp"), "-o", str(binary),
            ], check=True)
            result = subprocess.run([str(binary)], text=True, capture_output=True,
                                    check=True)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[:2], [r'\:\\"\x00\x0a\u4e2d\ud83e\udd8a',
                                   '-0:0.123457:-1:18446744073709551615'])
        frames = [json.loads(line.removeprefix("CAMOUFOX_TRACE ")) for line in lines[2:]]
        self.assertEqual([frame["part"] for frame in frames], list(range(7)))
        self.assertEqual([frame["final"] for frame in frames], [False] * 6 + [True])
        self.assertTrue(all((f["pid"], f["tid"], f["record"]) == (11, 22, 33)
                            for f in frames))
        self.assertEqual("".join(frame["data"] for frame in frames),
                         '$12:"http\\://127.0.0.1/script.js":' +
                         '%' * 1025 + '\\' * 1025 + '"' * 1025)


if __name__ == "__main__":
    unittest.main()
