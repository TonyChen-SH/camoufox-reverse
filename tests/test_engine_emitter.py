import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from engine_emit_patch import ATOM_HOOK, ELEMENT_HOOK, OBSERVE_DECLARATION, OBSERVE_HELPER, PATHS, transform


def cpp_function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class EngineEmitterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if "FIREFOX_SOURCE" not in os.environ:
            raise RuntimeError("Set FIREFOX_SOURCE to the pinned Firefox sources")
        cls.source = Path(os.environ["FIREFOX_SOURCE"])

    def read(self, path):
        return (self.source / path).read_bytes()

    def compile_and_run(self, source):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / "check.cpp"
            binary = root / "check"
            file.write_text(source)
            subprocess.run([os.environ.get("CXX", "g++"), "-std=c++20", "-Wall", "-Wextra",
                            "-Werror", "-fsanitize=undefined", "-fno-sanitize-recover=all",
                            str(file), "-o", str(binary)], check=True, capture_output=True)
            return subprocess.run([str(binary)], check=True, capture_output=True, text=True).stdout

    def test_transforms_all_pinned_sources_and_refuses_changed_anchors(self):
        for path in PATHS:
            with self.subTest(path=path):
                original = self.read(path)
                updated = transform(path, original)
                self.assertNotEqual(original, updated)
                with self.assertRaisesRegex(ValueError, "anchor"):
                    transform(path, b"// unrelated source\n")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            transform("not/a/source.cpp", b"")

    def test_property_hooks_cover_all_operand_layouts_without_name_lookup(self):
        for operation in ("GetProp", "GetPropSuper", "SetProp", "StrictSetProp",
                          "SetPropSuper", "StrictSetPropSuper"):
            self.assertIn(f"case JSOp::{operation}:", ATOM_HOOK)
        for operation in ("GetElem", "GetElemSuper", "SetElem", "StrictSetElem",
                          "SetElemSuper", "StrictSetElemSuper", "In"):
            self.assertIn(f"case JSOp::{operation}:", ELEMENT_HOOK)
        for operation in ("GetName", "GetGName", "GetBoundName", "SetName", "SetGName"):
            self.assertNotIn(f"case JSOp::{operation}:", ATOM_HOOK)
        self.assertNotIn("emitTree", OBSERVE_HELPER)
        self.assertNotIn("ToPropertyKey", OBSERVE_HELPER)

    def test_reflect_hooks_preserve_existing_key_conversion(self):
        path = "js/src/builtin/Reflect.cpp"
        original = self.read(path).decode()
        updated = transform(path, self.read(path)).decode()
        self.assertEqual(updated.count("ToPropertyKey("), original.count("ToPropertyKey("))
        method = cpp_function(updated, "static bool Reflect_set(")
        self.assertLess(method.index("ToPropertyKey("), method.index("CamoufoxMonitorProperty("))
        self.assertLess(method.index("CamoufoxMonitorProperty("), method.index("SetProperty("))
        self.assertIn("IdToValue(key)", method)
        self.assertIn("IdToString(cx, key)", method)
        self.assertIn("saved.restore()", method)
        self.assertIn('CamoufoxMonitorFailure("engine_reflect_key")', method)
        self.assertLess(method.index("if (key.isInt())"), method.index("IdToString(cx, key)"))
        path = "js/src/builtin/Reflect.js"
        updated = transform(path, self.read(path)).decode()
        self.assertEqual(updated.count("CamoufoxObserve(0, target, propertyKey, undefined);"), 1)
        self.assertEqual(updated.count("CamoufoxObserve(2, target, propertyKey, undefined);"), 1)

    def test_private_intrinsic_remains_noninlinable_and_sorted(self):
        path = "js/src/vm/SelfHosting.cpp"
        updated = transform(path, self.read(path)).decode()
        self.assertIn('JS_FN("CamoufoxObserve", intrinsic_CamoufoxObserve, 4, 0)', updated)
        self.assertLess(updated.index('JS_FN("CallWeakMapMethodIfWrapped"'),
                        updated.index('JS_FN("CamoufoxObserve"'))
        self.assertLess(updated.index('JS_FN("CamoufoxObserve"'),
                        updated.index('JS_FN("CanBeHeldWeakly"'))
        path = "js/src/frontend/BytecodeEmitter.cpp"
        updated = transform(path, self.read(path)).decode()
        self.assertIn("valueUsage == ValueUsage::IgnoreValue || emit1(JSOp::Undefined)", updated)

    def test_real_observer_helper_preserves_stack_and_copies_correct_values(self):
        declaration = OBSERVE_DECLARATION.replace("[[nodiscard]] ", "")
        program = r'''
#include <cassert>
#include <cstdint>
#include <iostream>
#include <optional>
#include <string>
#include <vector>
namespace mozilla {
template<typename T> using Maybe = std::optional<T>;
template<typename T> auto Some(T value) { return std::optional<T>(value); }
auto Nothing() { return std::nullopt; }
}
struct GCThingIndex { unsigned value; };
struct TaggedParserAtomIndex {
  explicit operator bool() const { return true; }
};
enum class JSOp { GetIntrinsic, Undefined, String, Call, Pop };
namespace js {
bool enabled = true;
bool CamoufoxMonitorEnabled() { return enabled; }
}
struct Atoms {
  TaggedParserAtomIndex internAscii(void*, const char*, unsigned) { return {}; }
};
class BytecodeEmitter {
 public:
  enum Mode { Normal, SelfHosting };
  Mode emitterMode = Normal;
  void* fc = nullptr;
  Atoms atoms;
  std::vector<std::string> stack;
  std::vector<std::string> seen;
  unsigned calls = 0;
  Atoms& parserAtoms() { return atoms; }
  bool emitAtomOp(JSOp op, TaggedParserAtomIndex) {
    assert(op == JSOp::GetIntrinsic); stack.push_back("observer"); return true;
  }
  bool emit1(JSOp op) {
    if (op == JSOp::Undefined) stack.push_back("undefined");
    else { assert(op == JSOp::Pop); stack.pop_back(); }
    return true;
  }
  bool emitNumberOp(unsigned value) { stack.push_back(std::to_string(value)); return true; }
  bool emitDupAt(unsigned depth) {
    assert(depth < stack.size());
    std::string value = stack[stack.size() - 1 - depth];
    stack.push_back(value); return true;
  }
  bool emitStringOp(JSOp op, GCThingIndex index) {
    assert(op == JSOp::String); assert(index.value == 7);
    stack.push_back("named-key"); return true;
  }
  bool emitCall(JSOp op, unsigned argc) {
    assert(op == JSOp::Call); assert(argc == 4);
    const auto start = stack.size() - argc - 2;
    assert(stack[start] == "observer" && stack[start + 1] == "undefined");
    seen.assign(stack.begin() + start + 2, stack.end());
    stack.resize(start); stack.push_back("result"); calls++; return true;
  }
''' + declaration + r'''
};
''' + OBSERVE_HELPER + r'''
int main() {
  auto check = [](std::vector<std::string> original, unsigned op,
                  unsigned receiver, mozilla::Maybe<GCThingIndex> atom,
                  unsigned key, mozilla::Maybe<unsigned> value,
                  std::vector<std::string> expected) {
    BytecodeEmitter emitter;
    emitter.stack = original;
    assert(emitter.emitCamoufoxObserve(op, receiver, atom, key, value));
    assert(emitter.stack == original);
    assert(emitter.calls == 1 && emitter.seen == expected);
  };
  auto atom = mozilla::Some(GCThingIndex{7});
  auto none = mozilla::Nothing();
  check({"OBJ"}, 0, 0, atom, 0, none, {"0", "OBJ", "named-key", "undefined"});
  check({"OLD", "OBJ", "NEW"}, 1, 1, atom, 0, mozilla::Some(0u), {"1", "OBJ", "named-key", "NEW"});
  check({"OBJ", "KEY"}, 0, 1, none, 0, none, {"0", "OBJ", "KEY", "undefined"});
  check({"OBJ", "KEY", "OBJ", "KEY"}, 0, 1, none, 0, none, {"0", "OBJ", "KEY", "undefined"});
  check({"OLD", "OBJ", "KEY", "NEW"}, 1, 2, none, 1, mozilla::Some(0u), {"1", "OBJ", "KEY", "NEW"});
  check({"THIS", "BASE"}, 0, 0, atom, 0, none, {"0", "BASE", "named-key", "undefined"});
  check({"THIS", "KEY", "BASE"}, 0, 0, none, 1, none, {"0", "BASE", "KEY", "undefined"});
  check({"THIS", "BASE", "NEW"}, 1, 1, atom, 0, mozilla::Some(0u), {"1", "BASE", "named-key", "NEW"});
  check({"THIS", "KEY", "BASE", "NEW"}, 1, 1, none, 2, mozilla::Some(0u), {"1", "BASE", "KEY", "NEW"});
  check({"KEY", "OBJ"}, 2, 0, none, 1, none, {"2", "OBJ", "KEY", "undefined"});
  BytecodeEmitter disabled;
  disabled.stack = {"OBJ"};
  js::enabled = false;
  assert(disabled.emitCamoufoxObserve(0, 0, atom, 0, none));
  assert(disabled.stack == std::vector<std::string>{"OBJ"} && disabled.calls == 0);
  js::enabled = true;
  disabled.emitterMode = BytecodeEmitter::SelfHosting;
  assert(disabled.emitCamoufoxObserve(0, 0, atom, 0, none));
  assert(disabled.stack == std::vector<std::string>{"OBJ"} && disabled.calls == 0);
  std::cout << "10 operand layouts and OFF/self-hosting gates passed\n";
}
'''
        self.assertIn("10 operand layouts", self.compile_and_run(program))

    def test_real_build_id_function_separates_monitoring_mode(self):
        path = "js/src/frontend/StencilXdr.cpp"
        updated = transform(path, self.read(path)).decode()
        function = cpp_function(updated, "JS_PUBLIC_API bool JS::GetScriptTranscodingBuildId(")
        program = r'''
#include <bit>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <string>
#define MOZ_ASSERT(value) assert(value)
#define JS_PUBLIC_API
namespace js {
bool enabled = false;
bool CamoufoxMonitorEnabled() { return enabled; }
}
namespace JS {
struct BuildIdCharVector {
  std::string data;
  bool empty() const { return data.empty(); }
  unsigned length() const { return data.length(); }
  bool reserve(unsigned size) { data.reserve(size); return true; }
  void infallibleAppend(char value) { data.push_back(value); }
};
bool GetScriptTranscodingBuildId(BuildIdCharVector*);
}
bool GetBuildId(JS::BuildIdCharVector* vector) { vector->data = "browser"; return true; }
''' + function + r'''
int main() {
  JS::BuildIdCharVector off, on;
  assert(JS::GetScriptTranscodingBuildId(&off));
  js::enabled = true;
  assert(JS::GetScriptTranscodingBuildId(&on));
  std::string expected = "browser-";
  expected += sizeof(uintptr_t) == 4 ? '4' : '8';
  expected += std::endian::native == std::endian::little ? 'l' : 'b';
  assert(off.data == expected);
  assert(on.data == off.data + "-cfm1");
  std::cout << "OFF build ID unchanged; ON build ID partitioned\n";
}
'''
        self.assertIn("OFF build ID unchanged", self.compile_and_run(program))


if __name__ == "__main__":
    unittest.main()
