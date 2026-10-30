"""Execute real statement/void consumers around the injected self-hosting gate."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
from engine_emit_patch import SELF_HOSTING_GATE, transform
from test_engine_emitter import cpp_function


PRELUDE = r'''
#include <cassert>
#include <cstdint>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#define DEBUG
#define MOZ_ASSERT(value) assert(value)
#define MOZ_STACK_CLASS
namespace js {
bool enabled = false;
bool CamoufoxMonitorEnabled() { return enabled; }
}
enum class ValueUsage { WantValue, IgnoreValue };
enum class JSOp { Undefined, Pop, SetRval, Void, Call, Goto };
enum class TryNoteKind { Loop };
enum class ParseNodeKind { ExpressionStmt, CallExpr, Name, VoidExpr };
struct Position { uint32_t begin = 17; };
struct ParseNode {
  ParseNodeKind kind;
  Position pn_pos;
  explicit ParseNode(ParseNodeKind value): kind(value) {}
  ParseNodeKind getKind() const { return kind; }
  bool isKind(ParseNodeKind value) const { return kind == value; }
  template<typename T> T& as() { return static_cast<T&>(*this); }
};
struct TaggedParserAtomIndex {
  unsigned id = 0;
  explicit operator bool() const { return id != 0; }
  bool operator==(TaggedParserAtomIndex other) const { return id == other.id; }
};
struct NameNode : ParseNode {
  TaggedParserAtomIndex atom;
  explicit NameNode(unsigned id): ParseNode(ParseNodeKind::Name), atom{id} {}
  TaggedParserAtomIndex name() { return atom; }
};
struct CallNode : ParseNode {
  NameNode name;
  explicit CallNode(unsigned id): ParseNode(ParseNodeKind::CallExpr), name(id) {}
  ParseNode* callee() { return &name; }
  JSOp callOp() const { return JSOp::Call; }
};
struct UnaryNode : ParseNode {
  ParseNode* child;
  UnaryNode(ParseNodeKind kind, ParseNode* expression): ParseNode(kind), child(expression) {}
  ParseNode* kid() { return child; }
};
struct Atoms {
  bool fail = false;
  TaggedParserAtomIndex internAscii(void*, const char*, unsigned) { return {fail ? 0u : 1u}; }
};
struct Context {
  bool top = false, noRval = true;
  bool isTopLevelContext() const { return top; }
  bool noScriptRval() const { return noRval; }
};
struct LabelControl { uint32_t startOffset() const { return 0; } };
struct NestableControl {
  template<typename T> bool is() const { return false; }
  template<typename T> const T& as() const { static T value; return value; }
};
JSOp UnaryOpParseNodeKindToJSOp(ParseNodeKind kind) {
  assert(kind == ParseNodeKind::VoidExpr); return JSOp::Void;
}
struct BytecodeEmitter {
  enum Mode { Normal, SelfHosting };
  Mode emitterMode = SelfHosting;
  void* fc = nullptr;
  Context context;
  Context* sc = &context;
  NestableControl* innermostNestableControl = nullptr;
  Atoms atoms;
  std::vector<JSOp> code;
  int depth = 0;
  unsigned coordinates = 0, breakpoints = 0, nativeCalls = 0;
  Atoms& parserAtoms() { return atoms; }
  BytecodeEmitter& bytecodeSection() { return *this; }
  int stackDepth() const { return depth; }
  uint32_t offset() const { return code.size(); }
  bool updateSourceCoordNotes(uint32_t) { coordinates++; return true; }
  bool markStepBreakpoint() { breakpoints++; return true; }
  bool checkSideEffects(ParseNode*, bool* useful) { *useful = true; return true; }
  bool emit1(JSOp op) {
    if (op == JSOp::Undefined) depth++;
    else {
      if (depth < 1) throw std::runtime_error("expression consumer stack underflow");
      if (op == JSOp::Pop || op == JSOp::SetRval) depth--;
      else assert(op == JSOp::Void);
    }
    code.push_back(op); return true;
  }
  bool emitCallOrNew(CallNode*, ValueUsage);
  bool emitExpressionStatement(UnaryNode*);
  bool emitUnary(UnaryNode*);
  bool emitTree(ParseNode* node, ValueUsage usage = ValueUsage::WantValue) {
    if (node->isKind(ParseNodeKind::CallExpr)) return emitCallOrNew(&node->as<CallNode>(), usage);
    assert(node->isKind(ParseNodeKind::VoidExpr));
    return emitUnary(&node->as<UnaryNode>());
  }
};
struct LoopInfo {
  bool emitLoopEnd(BytecodeEmitter*, JSOp, TryNoteKind) { return true; }
};
struct CForEmitter {
  enum class Update { Present, Missing };
  enum class Cond { Present, Missing };
  enum class State { Update, End };
  BytecodeEmitter* bce_;
  Update update_ = Update::Present;
  Cond cond_ = Cond::Present;
  State state_ = State::Update;
  std::unique_ptr<int> tdzCache_;
  std::unique_ptr<LoopInfo> loopInfo_ = std::make_unique<LoopInfo>();
  explicit CForEmitter(BytecodeEmitter* bce): bce_(bce) {}
  bool emitEnd(uint32_t);
};
'''

MAIN = r'''
int main(int argc, char** argv) {
  assert(argc == 2);
  std::string scenario = argv[1];
  BytecodeEmitter bce;
  CallNode observer(1), unrelated(2);
  UnaryNode statement(ParseNodeKind::ExpressionStmt, &observer);
  if (scenario == "statement") {
    assert(bce.emitExpressionStatement(&statement));
    assert(bce.depth == 0 && bce.code.empty() && bce.nativeCalls == 0);
    assert(bce.coordinates == 0 && bce.breakpoints == 0);
  } else if (scenario == "ignore-contexts") {
    assert(bce.emitTree(&observer, ValueUsage::IgnoreValue) && bce.depth == 1);
    assert(bce.emit1(JSOp::Pop) && bce.depth == 0); // for-init caller consumes its value.
    assert(bce.emitTree(&observer, ValueUsage::IgnoreValue) && bce.depth == 1);
    CForEmitter loop(&bce);
    assert(loop.emitEnd(0) && bce.depth == 0);
    UnaryNode discarded(ParseNodeKind::VoidExpr, &observer);
    UnaryNode voidStatement(ParseNodeKind::ExpressionStmt, &discarded);
    assert(bce.emitExpressionStatement(&voidStatement) && bce.depth == 0);
    assert(bce.nativeCalls == 0);
  } else if (scenario == "want-value") {
    assert(bce.emitTree(&observer, ValueUsage::WantValue) && bce.depth == 1);
    assert(bce.code == std::vector<JSOp>{JSOp::Undefined});
    assert(bce.emit1(JSOp::Pop));
    bce.code.clear();
    bce.context.top = true; bce.context.noRval = false;
    assert(bce.emitExpressionStatement(&statement));
    assert(bce.code == (std::vector<JSOp>{JSOp::Undefined, JSOp::SetRval}) && bce.depth == 0);
  } else if (scenario == "on-and-normal") {
    js::enabled = true;
    assert(bce.emitExpressionStatement(&statement) && bce.nativeCalls == 1 && bce.depth == 0);
    js::enabled = false;
    bce.emitterMode = BytecodeEmitter::Normal;
    assert(bce.emitExpressionStatement(&statement) && bce.nativeCalls == 2 && bce.depth == 0);
    bce.emitterMode = BytecodeEmitter::SelfHosting;
    UnaryNode regular(ParseNodeKind::ExpressionStmt, &unrelated);
    assert(bce.emitExpressionStatement(&regular) && bce.nativeCalls == 3 && bce.depth == 0);
  } else if (scenario == "allocation-failure") {
    bce.atoms.fail = true;
    assert(!bce.emitExpressionStatement(&statement));
    assert(bce.depth == 0 && bce.code.empty() && bce.nativeCalls == 0);
  } else return 2;
  std::cout << scenario << " passed\n";
}
'''


class SelfHostingExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(os.environ["FIREFOX_SOURCE"])
        upstream = source / "js/src/frontend/ExpressionStatementEmitter.cpp"
        provider = upstream.read_text()
        header = (source / "js/src/frontend/ExpressionStatementEmitter.h").read_text()
        original = (source / "js/src/frontend/BytecodeEmitter.cpp").read_bytes()
        updated = transform("js/src/frontend/BytecodeEmitter.cpp", original).decode()
        statement = cpp_function(updated, "bool BytecodeEmitter::emitExpressionStatement(")
        unary = cpp_function(original.decode(), "bool BytecodeEmitter::emitUnary(")
        for_end = cpp_function((source / "js/src/frontend/CForEmitter.cpp").read_text(),
                               "bool CForEmitter::emitEnd(")
        gate = cpp_function(updated, "bool BytecodeEmitter::emitCallOrNew(")
        if SELF_HOSTING_GATE not in gate:
            raise AssertionError("The model must use the real injected call gate")
        expression_class = cpp_function(header, "class MOZ_STACK_CLASS ExpressionStatementEmitter") + ";\n"
        methods = "\n".join(cpp_function(provider, signature) for signature in (
            "ExpressionStatementEmitter::ExpressionStatementEmitter(",
            "bool ExpressionStatementEmitter::prepareForExpr(",
            "bool ExpressionStatementEmitter::emitEnd("))
        call = '''bool BytecodeEmitter::emitCallOrNew(CallNode* node, ValueUsage valueUsage) {
  (void)valueUsage;
  if (emitterMode == SelfHosting && node->callOp() == JSOp::Call) {
    auto calleeName = node->callee()->as<NameNode>().name();
''' + SELF_HOSTING_GATE + '''  }
  nativeCalls++;
  return emit1(JSOp::Undefined);
}
'''
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cpp = Path(cls.temporary.name) / "expression.cpp"
        cls.binary = Path(cls.temporary.name) / "expression"
        cpp.write_text(PRELUDE + expression_class + methods + "\n" + call + statement + "\n" +
                       unary + "\n" + for_end + MAIN)
        result = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++20", "-Wall", "-Wextra",
                                 "-Werror", "-fsanitize=undefined", "-fno-sanitize-recover=all",
                                 str(cpp), "-o", str(cls.binary)], text=True, capture_output=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def scenario(self, name):
        result = subprocess.run([str(self.binary), name], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(name + " passed", result.stdout)

    def test_disabled_standalone_statement_emits_no_monitor_bytecode(self):
        self.scenario("statement")

    def test_discarded_loop_and_void_consumers_receive_one_value(self):
        self.scenario("ignore-contexts")

    def test_required_value_and_script_result_keep_undefined(self):
        self.scenario("want-value")

    def test_enabled_normal_and_unrelated_calls_keep_their_consumers(self):
        self.scenario("on-and-normal")

    def test_atom_allocation_failure_propagates_without_underflow(self):
        self.scenario("allocation-failure")
