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
            result = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++20", "-Wall", "-Wextra",
                                     "-Werror", "-fsanitize=undefined", "-fno-sanitize-recover=all",
                                     str(file), "-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
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

    def test_operation_source_coordinates_are_owned_and_threaded_to_the_observer_call(self):
        for kind in ("Prop", "Elem"):
            path = f"js/src/frontend/{kind}OpEmitter.h"
            self.assertIn(path, PATHS)
            updated = transform(path, self.read(path)).decode()
            self.assertIn("mozilla::Maybe<uint32_t> camoufoxSourceCoord_", updated)
            self.assertIn("void setCamoufoxSourceCoord(", updated)
            path = f"js/src/frontend/{kind}OpEmitter.cpp"
            updated = transform(path, self.read(path)).decode()
            for name in ("emitGet", "emitAssignment", "emitIncDec"):
                method = cpp_function(updated, f"bool {kind}OpEmitter::{name}(")
                self.assertIn("camoufoxSourceCoord_)", method)
        path = "js/src/frontend/BytecodeEmitter.cpp"
        updated = transform(path, self.read(path)).decode()
        for name in ("emitPropIncDec", "emitDestructuringLHSRef", "emitAssignmentOrInit",
                     "emitShortCircuitAssignment", "emitCalleeAndThis",
                     "emitOptionalDotExpression", "emitOptionalElemExpression", "emitElemObjAndKey"):
            method = cpp_function(updated, f"bool BytecodeEmitter::{name}(")
            self.assertIn("setCamoufoxSourceCoord(", method)
            self.assertLess(method.index("setCamoufoxSourceCoord("), method.index("prepareForObj()"))
        method = cpp_function(updated, "bool BytecodeEmitter::emitPropLHS(")
        self.assertIn("Some(pndot->pn_pos.begin)", method)
        method = cpp_function(updated, "bool BytecodeEmitter::emitLeftAssociative(")
        self.assertIn("JSOp::In", method)
        self.assertIn("Some(node->pn_pos.begin)", method)
        self.assertIn("emitCall(JSOp::Call, 4, sourceCoordOffset)", OBSERVE_HELPER)

    def test_coordinate_owner_anchor_changes_fail_loudly(self):
        path = "js/src/frontend/BytecodeEmitter.cpp"
        original = self.read(path).decode()
        for name in ("emitOptionalDotExpression", "emitElemObjAndKey", "emitAssignmentOrInit"):
            with self.subTest(function=name):
                method = cpp_function(original, f"bool BytecodeEmitter::{name}(")
                changed = method.replace("prepareForObj()", "prepareForObject()", 1)
                self.assertNotEqual(method, changed)
                with self.assertRaisesRegex(ValueError, "anchor"):
                    transform(path, original.replace(method, changed, 1).encode())

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
    def test_real_observer_helper_preserves_stack_and_copies_correct_values(self):
        declaration = OBSERVE_DECLARATION.replace("[[nodiscard]] ", "")
        call = cpp_function(self.read("js/src/frontend/BytecodeEmitter.cpp").decode(),
                            "bool BytecodeEmitter::emitCall(JSOp op, uint16_t argc,")
        program = r'''
#include <cassert>
#include <cstdint>
#include <iostream>
#include <optional>
#include <string>
#include <vector>
namespace mozilla {
template<typename T> struct Maybe : std::optional<T> {
  using std::optional<T>::optional;
  bool isSome() const { return this->has_value(); }
};
template<typename T> auto Some(T value) { return Maybe<T>(value); }
auto Nothing() { return std::nullopt; }
}
using mozilla::Maybe;
#define ARGC_LO(argc) ((argc) & 0xff)
#define ARGC_HI(argc) (((argc) >> 8) & 0xff)
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
  uint32_t currentSourceCoord = 999;
  uint32_t seenSourceCoord = 0;
  std::vector<uint32_t> notes;
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
  bool updateSourceCoordNotes(uint32_t offset) {
    notes.push_back(offset); currentSourceCoord = offset; return true;
  }
  bool emitCall(JSOp, uint16_t, const Maybe<uint32_t>& = mozilla::Nothing());
  bool emit3(JSOp op, unsigned low, unsigned high) {
    const unsigned argc = low | high << 8;
    assert(op == JSOp::Call); assert(argc == 4);
    const auto start = stack.size() - argc - 2;
    assert(stack[start] == "observer" && stack[start + 1] == "undefined");
    seen.assign(stack.begin() + start + 2, stack.end());
    seenSourceCoord = currentSourceCoord;
    stack.resize(start); stack.push_back("result"); calls++; return true;
  }
''' + declaration + r'''
};
''' + call + '\n' + OBSERVE_HELPER + r'''
int main() {
  auto check = [](std::vector<std::string> original, unsigned op,
                  unsigned receiver, mozilla::Maybe<GCThingIndex> atom,
                  unsigned key, mozilla::Maybe<unsigned> value,
                  std::vector<std::string> expected) {
    BytecodeEmitter emitter;
    emitter.stack = original;
    assert(emitter.emitCamoufoxObserve(op, receiver, atom, key, value, mozilla::Some(1234u)));
    assert(emitter.stack == original);
    assert(emitter.calls == 1 && emitter.seen == expected);
    assert(emitter.notes == std::vector<uint32_t>{1234} && emitter.seenSourceCoord == 1234);
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
  assert(disabled.emitCamoufoxObserve(0, 0, atom, 0, none, mozilla::Some(77u)));
  assert(disabled.stack == std::vector<std::string>{"OBJ"} && disabled.calls == 0);
  assert(disabled.notes.empty() && disabled.currentSourceCoord == 999);
  js::enabled = true;
  disabled.emitterMode = BytecodeEmitter::SelfHosting;
  assert(disabled.emitCamoufoxObserve(0, 0, atom, 0, none, mozilla::Some(88u)));
  assert(disabled.stack == std::vector<std::string>{"OBJ"} && disabled.calls == 0);
  assert(disabled.notes.empty() && disabled.currentSourceCoord == 999);
  BytecodeEmitter legacy;
  legacy.stack = {"OBJ"};
  assert(legacy.emitCamoufoxObserve(0, 0, atom, 0, none));
  assert(legacy.notes.empty() && legacy.seenSourceCoord == 999);
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
  assert(on.data == off.data + "-cfm2");
  assert(on.data != off.data + "-cfm1");
  std::cout << "OFF build ID unchanged; ON build ID partitioned\n";
}
'''
        self.assertIn("OFF build ID unchanged", self.compile_and_run(program))

    def test_real_property_emitters_retain_coordinates_across_rhs_and_moves(self):
        classes, methods = [], []
        for kind in ("Prop", "Elem"):
            name = kind + "OpEmitter"
            path = f"js/src/frontend/{name}.h"
            header = transform(path, self.read(path)).decode()
            path = f"js/src/frontend/{name}.cpp"
            implementation = transform(path, self.read(path)).decode()
            helpers = "\n".join(cpp_function(header, "bool " + helper + "(") for helper in (
                "isCall", "isSuper", "isSimpleAssignment", "isPropInit", "isCompoundAssignment",
                "isIncDec", "isPostIncDec", "isPreIncDec", "isInc"))
            arguments = "TaggedParserAtomIndex" if kind == "Prop" else ""
            inc_arguments = "TaggedParserAtomIndex, ValueUsage" if kind == "Prop" else "ValueUsage"
            classes.append(f"""class {name} {{
 public:
  {cpp_function(header, 'enum class Kind')} ;
  {cpp_function(header, 'enum class ObjKind')} ;
  BytecodeEmitter* bce_;
  Kind kind_;
  ObjKind objKind_;
  mozilla::Maybe<uint32_t> camoufoxSourceCoord_;
  GCThingIndex propAtomIndex_ = 7;
  {name}(BytecodeEmitter*, Kind, ObjKind);
  {cpp_function(header, 'void setCamoufoxSourceCoord(')}
  {helpers}
  bool prepareAtomIndex(TaggedParserAtomIndex) {{ return true; }}
  bool emitGet({arguments});
  bool emitAssignment({arguments});
  bool emitIncDec({inc_arguments});
}};""")
            methods.append(cpp_function(implementation, f"{name}::{name}("))
            methods.extend(cpp_function(implementation, f"bool {name}::{method}(")
                           for method in ("emitGet", "emitAssignment", "emitIncDec"))
        program = r'''
#include <cassert>
#include <cstdint>
#include <iostream>
#include <optional>
#include <utility>
#include <vector>
#define MOZ_ASSERT(...) ((void)0)
#define MOZ_ASSERT_IF(...) ((void)0)
namespace mozilla {
template<typename T> using Maybe = std::optional<T>;
template<typename T> auto Some(T value) { return std::optional<T>(value); }
}
using GCThingIndex = unsigned;
using TaggedParserAtomIndex = unsigned;
enum class ValueUsage { WantValue, IgnoreValue };
enum class JSOp { GetProp, GetPropSuper, InitProp, StrictSetPropSuper, SetPropSuper,
  StrictSetProp, SetProp, ToNumeric, Inc, Dec, Dup, Dup2, Unpick, Pop, Swap,
  ToPropertyKey, GetElem, GetElemSuper, InitElem, StrictSetElemSuper, SetElemSuper,
  StrictSetElem, SetElem };
struct Context { bool strict() { return false; } };
struct BytecodeEmitter {
  Context context;
  Context* sc = &context;
  uint32_t currentSourceCoord = 999;
  std::vector<mozilla::Maybe<uint32_t>> coordinates;
  bool record(const mozilla::Maybe<uint32_t>& coordinate) {
    coordinates.push_back(coordinate);
    if (coordinate) currentSourceCoord = *coordinate;
    return true;
  }
  bool emitAtomOp(JSOp, GCThingIndex, const mozilla::Maybe<uint32_t>& coordinate) {
    return record(coordinate);
  }
  bool emitElemOpBase(JSOp, const mozilla::Maybe<uint32_t>& coordinate) { return record(coordinate); }
  bool emitSuperBase() { return true; }
  bool emitDupAt(unsigned, unsigned) { return true; }
  bool emit1(JSOp) { return true; }
  bool emit2(JSOp, unsigned) { return true; }
};
''' + '\n'.join(classes + methods) + r'''
int main() {
  using P = PropOpEmitter;
  using E = ElemOpEmitter;
  auto check = [](const BytecodeEmitter& bce, std::initializer_list<uint32_t> expected) {
    assert(bce.coordinates.size() == expected.size());
    unsigned index = 0;
    for (uint32_t coordinate : expected) {
      assert(bce.coordinates[index] && *bce.coordinates[index] == coordinate);
      index++;
    }
  };
  BytecodeEmitter named;
  P compound(&named, P::Kind::CompoundAssignment, P::ObjKind::Other);
  compound.setCamoufoxSourceCoord(mozilla::Some(17u));
  named.currentSourceCoord = 5000; // receiver evaluation changed notes.
  assert(compound.emitGet(7));
  named.currentSourceCoord = 9000; // RHS evaluation changed notes.
  assert(compound.emitAssignment(7));
  check(named, {17,17});
  BytecodeEmitter keyed;
  E keyCompound(&keyed, E::Kind::CompoundAssignment, E::ObjKind::Super);
  keyCompound.setCamoufoxSourceCoord(mozilla::Some(31u));
  keyed.currentSourceCoord = 6000; // receiver/key evaluation changed notes.
  assert(keyCompound.emitGet());
  keyed.currentSourceCoord = 9999;
  assert(keyCompound.emitAssignment());
  check(keyed, {31,31});
  BytecodeEmitter counted;
  P namedCount(&counted, P::Kind::PostIncrement, P::ObjKind::Super);
  namedCount.setCamoufoxSourceCoord(mozilla::Some(44u));
  assert(namedCount.emitIncDec(7, ValueUsage::WantValue));
  E keyedCount(&counted, E::Kind::PreDecrement, E::ObjKind::Other);
  keyedCount.setCamoufoxSourceCoord(mozilla::Some(70u));
  assert(keyedCount.emitIncDec(ValueUsage::WantValue));
  check(counted, {44,44,70,70});
  BytecodeEmitter destructuring;
  P source(&destructuring, P::Kind::SimpleAssignment, P::ObjKind::Other);
  source.setCamoufoxSourceCoord(mozilla::Some(82u));
  auto moved = std::move(source);
  destructuring.currentSourceCoord = 12345;
  assert(moved.emitAssignment(7));
  check(destructuring, {82});
  BytecodeEmitter method;
  P first(&method, P::Kind::Call, P::ObjKind::Other);
  P second(&method, P::Kind::Get, P::ObjKind::Other);
  first.setCamoufoxSourceCoord(mozilla::Some(93u));
  second.setCamoufoxSourceCoord(mozilla::Some(110u));
  assert(first.emitGet(7) && second.emitGet(7));
  check(method, {93,110});
  BytecodeEmitter synthetic;
  E internal(&synthetic, E::Kind::Get, E::ObjKind::Other);
  assert(internal.emitGet());
  assert(synthetic.coordinates.size() == 1 && !synthetic.coordinates[0]);
  assert(synthetic.currentSourceCoord == 999);
  std::cout << "Owned coordinates survived receiver, key, RHS, count, callee and move paths\n";
}
'''
        self.assertIn("Owned coordinates survived", self.compile_and_run(program))


if __name__ == "__main__":
    unittest.main()
