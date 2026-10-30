"""Run the producer's source/context path against the pinned loading providers."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_engine_emitter import cpp_function


MODEL = r'''
#include <atomic>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>
#include "CamoufoxMonitorBuffer.h"
using js::camoufox::Vv8Record;
namespace mozilla {
template<typename T> struct Maybe : std::optional<T> {
  using std::optional<T>::optional;
  bool isNothing() const { return !this->has_value(); }
  bool isSome() const { return this->has_value(); }
};
struct Utf8Unit { char value; };
}
using mozilla::Utf8Unit;
using jsbytecode = unsigned char;
struct JSObject { template<typename T> T& as() { return static_cast<T&>(*this); } };
struct JSScript;
namespace js { struct ScriptSource; struct ScriptSourceObject; using BaseScript = JSScript; }
struct JSRuntime;
struct JSContext {
  bool system = false, exception = false, oom = false;
  bool hookError = false, hookOOM = false, substringError = false, linearError = false;
  bool appendError = false, filenameError = false;
  JSScript* frame = nullptr;
  JSRuntime* runtime_ = nullptr;
  struct Realm {
    JSContext* context;
    bool isSystem() const { return context->system; }
  } realm_{this};
  Realm* realm() { return &realm_; }
  JSRuntime* runtime() { return runtime_; }
  void* compartment() { return runtime_; }
  bool isExceptionPending() const { return exception; }
  bool isThrowingOutOfMemory() const { return oom; }
  void clearPendingException() { exception = false; oom = false; }
};
namespace JS {
struct Value {
  js::BaseScript* script = nullptr;
  std::string encoded;
  bool isUndefined() const { return !script; }
};
Value UndefinedValue() { return {}; }
template<typename T> using Handle = T;
using HandleObject = JSObject*;
using HandleValue = const Value&;
using HandleScript = JSScript*;
struct InstantiateOptions { bool hideScriptFromDebugger = true; };
struct AutoCheckCannotGC {};
struct ConstUTF8CharsZ { const char* text; explicit ConstUTF8CharsZ(const char* value): text(value) {} };
class LimitedColumnNumberOneOrigin {
  uint32_t value_ = 1;
 public:
  explicit LimitedColumnNumberOneOrigin(uint32_t value = 1): value_(value) {}
  uint32_t oneOriginValue() const { return value_; }
};
}
struct JSString {
  std::u16string text;
  bool failLinear = false;
  JSContext* cx = nullptr;
  size_t length() const { return text.size(); }
  JSString* ensureLinear(JSContext* context) {
    if (failLinear) { context->exception = context->oom = true; return nullptr; }
    return this;
  }
  bool hasLatin1Chars() const { return false; }
  const unsigned char* latin1Chars(JS::AutoCheckCannotGC&) const { return nullptr; }
  const char16_t* twoByteChars(JS::AutoCheckCannotGC&) const { return text.data(); }
};
using JSLinearString = JSString;
namespace JS {
using HandleString = JSString*;
template<typename T> struct MutableHandle {
  T* data;
  MutableHandle(T* value): data(value) {}
  template<typename U> MutableHandle(U* value): data(&value->data) {}
  void set(T value) { *data = value; }
};
template<typename T> struct Rooted {
  T data;
  Rooted(JSContext*, T value = T{}): data(value) {}
  operator T() const { return data; }
  T operator->() const { return data; }
  Rooted& operator=(T value) { data = value; return *this; }
  void set(T value) { data = value; }
  bool isUndefined() const { return data.isUndefined(); }
  void setPrivateGCThing(js::BaseScript* value) { data.script = value; }
};
using RootedObject = Rooted<JSObject*>;
using RootedScript = Rooted<JSScript*>;
using RootedString = Rooted<JSString*>;
using RootedValue = Rooted<Value>;
struct RootedObjectVector : std::vector<JSObject*> {
  JSContext* cx;
  explicit RootedObjectVector(JSContext* context): cx(context) {}
  bool append(JSObject* value) {
    if (cx->appendError) { cx->exception = cx->oom = true; return false; }
    push_back(value); return true;
  }
  size_t length() const { return size(); }
};
struct AutoSaveExceptionState {
  JSContext* cx;
  bool exception, oom;
  explicit AutoSaveExceptionState(JSContext* context): cx(context), exception(cx->exception), oom(cx->oom) {
    cx->clearPendingException();
  }
  void restore() { cx->exception = exception; cx->oom = oom; }
};
using CamoufoxMonitorContextCallback = bool (*)(JSContext*, MutableHandle<JSString*>, MutableHandle<JSString*>);
bool UpdateDebugMetadata(JSContext*, Handle<JSScript*>, const InstantiateOptions&, HandleValue,
                         HandleString, HandleScript, HandleScript);
}
std::vector<std::unique_ptr<JSString>> strings;
JSString* String(JSContext* cx, std::u16string value) {
  auto text = std::make_unique<JSString>();
  text->text = std::move(value); text->cx = cx; text->failLinear = cx->linearError;
  strings.push_back(std::move(text)); return strings.back().get();
}
JSString* JS_NewStringCopyUTF8Z(JSContext* cx, JS::ConstUTF8CharsZ chars) {
  if (cx->filenameError) { cx->exception = true; return nullptr; }
  std::u16string result;
  for (const char* p = chars.text; *p; ++p) result.push_back(static_cast<unsigned char>(*p));
  return String(cx, std::move(result));
}
namespace js {
struct SourceHook {
  virtual bool load(JSContext*, const char*, char16_t**, char**, size_t*) = 0;
};
}
struct JSRuntime {
  struct Hook {
    js::SourceHook* value = nullptr;
    js::SourceHook* ref() const { return value; }
    js::SourceHook* operator->() const { return value; }
  } sourceHook;
};
namespace js {
struct ScriptSource {
  uint32_t id_;
  bool loaded = true, retrievable = false, twoByte = true;
  std::u16string text;
  std::string filename_ = "source.js", introduction_;
  struct DataReader {
    ScriptSource* source_;
    explicit DataReader(ScriptSource* source): source_(source) {}
    explicit DataReader(mozilla::Maybe<DataReader>&& reader): source_(reader->source_) { reader.reset(); }
    bool hasSourceText() const { return source_->loaded; }
    bool isRetrievable() const { return source_->retrievable; }
    bool isTwoByteString() const { return source_->twoByte; }
    const ScriptSource* operator->() const { assert(hasSourceText()); return source_; }
  };
  explicit ScriptSource(uint32_t id, std::u16string body): id_(id), text(std::move(body)) {}
  uint32_t id() const { return id_; }
  uint32_t startLine() const { return 1; }
  JS::LimitedColumnNumberOneOrigin startColumn() const { return JS::LimitedColumnNumberOneOrigin(1); }
  bool hasIntroductionType() const { return !introduction_.empty(); }
  const char* introductionType() const { return introduction_.c_str(); }
  const char* filename() const { return filename_.empty() ? nullptr : filename_.c_str(); }
  size_t length() const { assert(loaded); return text.size(); }
  JSString* substring(JSContext* cx, size_t begin, size_t end) const {
    assert(loaded);
    if (cx->substringError) { cx->exception = cx->oom = true; return nullptr; }
    return String(cx, text.substr(begin, end - begin));
  }
  template<typename Unit>
  bool setRetrievedSource(JSContext* cx, mozilla::Maybe<DataReader>& input,
                          mozilla::Maybe<DataReader>& output, Unit* units, size_t length) {
    std::u16string result;
    for (size_t i = 0; i < length; ++i) {
      if constexpr (std::is_same_v<Unit, char16_t>) result.push_back(units[i]);
      else result.push_back(static_cast<unsigned char>(units[i].value));
    }
    delete[] units; input.reset();
    text = std::move(result); loaded = true; retrievable = false;
    output.emplace(this); (void)cx; return true;
  }
  SOURCE_LOADER_DECLARATION
};
struct ScriptSourceObject : JSObject {
  ScriptSource* source_;
  JS::Value intro;
  explicit ScriptSourceObject(ScriptSource* value): source_(value) {}
  ScriptSource* source() const { return source_; }
  BaseScript* unwrappedIntroductionScript() const { return intro.script; }
  void setIntroductionScript(const JS::Value& value) { intro = value; }
  JS::Value getPrivate() const { return {}; }
  void setPrivate(JSRuntime*, const JS::Value&) {}
  static bool initElementProperties(JSContext*, ScriptSourceObject*, JSString*) { return true; }
};
}
struct JSScript {
  js::ScriptSourceObject* source_;
  JSRuntime* runtime_;
  js::ScriptSourceObject* sourceObject() const { return source_; }
  js::ScriptSource* scriptSource() const { return source_->source(); }
  void* compartment() const { return runtime_; }
};
bool JS_WrapValue(JSContext*, JS::RootedValue*) { return true; }
namespace js::DebugAPI { void onNewScript(JSContext*, JSScript*) {} }
namespace js {
struct NonBuiltinScriptFrameIter {
  JSContext* cx;
  explicit NonBuiltinScriptFrameIter(JSContext* context): cx(context) {}
  bool done() const { return cx->frame == nullptr; }
  JSScript* script() const { return cx->frame; }
  jsbytecode* pc() const { static jsbytecode pc; return &pc; }
};
unsigned PCToLineNumber(JSScript*, jsbytecode*, JS::LimitedColumnNumberOneOrigin* column) {
  *column = JS::LimitedColumnNumberOneOrigin(3); return 1;
}
void CamoufoxMonitorProperty(JSContext*, uint32_t, JS::HandleValue, JS::HandleValue, JS::HandleValue);
}
namespace nsContentUtils { bool IsSystemCaller(JSContext* cx) { return cx->system; } }
using nsresult = int;
constexpr int NS_ERROR_FAILURE = 1, NS_ERROR_OUT_OF_MEMORY = 2;
unsigned sourceHookReads = 0;
nsresult ReadSourceFromFilename(JSContext* cx, const char*, char16_t** twoByte, char** utf8, size_t* length) {
  sourceHookReads++;
  if (cx->hookOOM) return NS_ERROR_OUT_OF_MEMORY;
  if (cx->hookError) return NS_ERROR_FAILURE;
  const std::u16string text = u"retrieved source \\n";
  *length = text.size();
  if (twoByte) {
    *twoByte = new char16_t[text.size()]; std::copy(text.begin(), text.end(), *twoByte);
  } else {
    *utf8 = new char[text.size()];
    for (size_t i = 0; i < text.size(); ++i) (*utf8)[i] = static_cast<char>(text[i]);
  }
  return 0;
}
namespace xpc {
void Throw(JSContext* cx, int error) { cx->exception = true; cx->oom = error == NS_ERROR_OUT_OF_MEMORY; }
}
#define MOZ_ASSERT(condition, ...) assert(condition)
#define NS_FAILED(value) ((value) != 0)
struct Config { bool enabled = true, privileged = true, valid = true; uint64_t maximum = 0; } config;
const Config& Configuration() { return config; }
std::atomic<JS::CamoufoxMonitorContextCallback> gContextCallback{nullptr};
std::atomic<uint64_t> gNextThread{1}, gRecords{0}, gEmitted{0};
std::atomic<bool> gStopped{false};
std::mutex gOutputMutex;
thread_local bool gRecording = false;
uint64_t ProcessId() { return 4758; }
bool HostObject(JS::HandleValue) { return true; }
bool AppendValue(JSContext*, JS::HandleValue value, Vv8Record& record) { record.Raw(value.encoded); return true; }
using namespace js;
using namespace JS;
'''

MAIN = r'''
int main(int argc, char** argv) {
  assert(argc == 2);
  std::string scenario = argv[1];
  JSRuntime runtime;
  XPCJSSourceHook hook;
  runtime.sourceHook.value = &hook;
  JSContext cx; cx.runtime_ = &runtime;
  js::ScriptSource source(11, u"full source \\n");
  js::ScriptSourceObject object(&source);
  JSScript script{&object, &runtime}; cx.frame = &script;
  js::ScriptSource parentSource(10, u"ancestor source");
  js::ScriptSourceObject parentObject(&parentSource);
  JSScript parent{&parentObject, &runtime};
  if (scenario == "missing" || scenario == "missing-repeated" || scenario == "long-missing") source.loaded = false;
  if (scenario == "empty-source") source.text.clear();
  if (scenario == "preexisting-exception") { source.loaded = false; cx.exception = true; }
  if (scenario == "no-script") cx.frame = nullptr;
  if (scenario == "long-missing") { source.filename_ = std::string(5000, '%'); source.introduction_ = std::string(2000, 'e'); }
  if (scenario == "retrievable" || scenario == "retrievable-utf8" || scenario == "retry" || scenario == "load-error" || scenario == "load-oom") {
    source.loaded = false; source.retrievable = true;
  }
  if (scenario == "retrievable" || scenario == "retrievable-utf8" || scenario == "load-error" || scenario == "load-oom") cx.system = true;
  if (scenario == "retrievable-utf8") source.twoByte = false;
  if (scenario == "load-error") cx.hookError = true;
  if (scenario == "load-oom") cx.hookOOM = true;
  if (scenario == "substring-oom") cx.substringError = true;
  if (scenario == "linear-oom") cx.linearError = true;
  if (scenario == "append-oom") cx.appendError = true;
  if (scenario == "filename-error") cx.filenameError = true;
  if (scenario == "missing-parent") {
    parentSource.loaded = false;
    assert(JS::UpdateDebugMetadata(&cx, &script, {}, {}, nullptr, &parent, nullptr));
  }
  if (scenario == "self-introduction") {
    assert(JS::UpdateDebugMetadata(&cx, &script, {}, {}, nullptr, &script, nullptr));
    assert(object.unwrappedIntroductionScript() == &script);
  }
  if (scenario == "two-introductions") {
    assert(JS::UpdateDebugMetadata(&cx, &script, {}, {}, nullptr, &parent, nullptr));
    assert(JS::UpdateDebugMetadata(&cx, &parent, {}, {}, nullptr, &script, nullptr));
  }
  if (scenario == "context-error" || scenario == "context-oom") {
    cx.hookOOM = scenario == "context-oom";
    gContextCallback.store(+[](JSContext* context, JS::MutableHandle<JSString*>, JS::MutableHandle<JSString*>) {
      context->exception = true; context->oom = context->hookOOM; return false;
    });
  }
  JS::Value receiver{nullptr, "{19,Navigator}"}, key{nullptr, "\"webdriver\""};
  js::CamoufoxMonitorProperty(&cx, 0, receiver, key, {});
  if (scenario == "retry") {
    cx.system = true;
    js::CamoufoxMonitorProperty(&cx, 0, receiver, key, {});
    js::CamoufoxMonitorProperty(&cx, 0, receiver, key, {});
  }
  if (scenario == "missing-repeated") {
    js::CamoufoxMonitorProperty(&cx, 0, receiver, key, {});
    js::CamoufoxMonitorProperty(&cx, 0, receiver, key, {});
  }
  std::cout << "hook_reads=" << sourceHookReads << ";pending=" << cx.exception << ";oom=" << cx.oom << '\n';
}
'''


class EngineSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(os.environ["FIREFOX_SOURCE"])
        producer = (ROOT / "additions/js/src/vm/CamoufoxMonitor.cpp").read_text()
        script_cpp = (source / "js/src/vm/JSScript.cpp").read_text()
        script_header = (source / "js/src/vm/JSScript.h").read_text()
        start = script_header.index("  bool tryLoadSource(")
        loader_declaration = script_header[start:script_header.index(";", start) + 1]
        runtime = (source / "js/xpconnect/src/XPCJSRuntime.cpp").read_text()
        compilation = (source / "js/src/vm/CompilationAndEvaluation.cpp").read_text()
        hook = runtime[runtime.index("class XPCJSSourceHook :"):]
        hook = hook[:hook.index("\n};") + 3]
        state = producer[producer.index("using SourceKey ="):producer.index("bool CanRecord(")]
        functions = []
        for signature in ("void AppendSourceDetails(", "void Limit(", "bool CanRecord(", "bool Emit(",
                          "bool AppendString(", "SourceKey Key(", "bool SourceFailure(",
                          "bool SourceUnavailable(", "bool RegisterSource(", "bool Context(",
                          "void js::CamoufoxMonitorProperty("):
            if signature in producer:
                functions.append(cpp_function(producer, signature))
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)
        cpp, cls.binary = root / "source.cpp", root / "source"
        cpp.write_text(MODEL.replace("SOURCE_LOADER_DECLARATION", loader_declaration) + hook + "\n" + cpp_function(script_cpp, "bool ScriptSource::tryLoadSource(")
                       + "\n" + cpp_function(compilation, "JS_PUBLIC_API bool JS::UpdateDebugMetadata(").replace("JS_PUBLIC_API ", "", 1)
                       + "\n" + state + "\n" + "\n".join(functions) + MAIN)
        compiled = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++20", "-Wall", "-Wextra", "-Werror",
                                   "-fsanitize=undefined", "-fno-sanitize-recover=all", "-I",
                                   str(ROOT / "additions/js/src/vm"), str(cpp), "-o", str(cls.binary)],
                                  text=True, capture_output=True)
        if compiled.returncode:
            raise RuntimeError(compiled.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def capture(self, scenario):
        result = subprocess.run([str(self.binary), scenario], text=True, capture_output=True, check=True)
        controls = [json.loads(line.removeprefix("CAMOUFOX_TRACE ")) for line in result.stderr.splitlines()]
        self.assertTrue(all(len(line.encode()) <= 4096 for line in result.stderr.splitlines()))
        raw = [item["data"] for item in controls if item["event"] == "vv8"]
        notices = [item for item in controls if item["event"] == "source_unavailable"]
        limits = [item for item in controls if item["event"] == "limit"]
        return raw, notices, limits, result.stdout.strip()

    def test_missing_source_preserves_api_with_unknown_script_and_explicit_provenance(self):
        raw, notices, limits, state = self.capture("missing")
        self.assertEqual(limits, [])
        self.assertIn("!?", raw)
        self.assertTrue(any(item.startswith("g-1:") for item in raw))
        self.assertFalse(any(item.startswith("$") for item in raw))
        self.assertEqual([(item["source_id"], item["reason"], item["retrievable"]) for item in notices],
                         [(11, "source_missing", False)])
        self.assertIn("pending=0;oom=0", state)

    def test_repeated_missing_source_never_stops_or_repeats_the_notice(self):
        raw, notices, limits, _ = self.capture("missing-repeated")
        self.assertEqual(limits, [])
        self.assertEqual(len(notices), 1)
        self.assertEqual(sum(item.startswith("g-1:") for item in raw), 3)

    def test_real_empty_source_is_not_confused_with_unavailable_source(self):
        raw, notices, limits, _ = self.capture("empty-source")
        self.assertEqual((notices, limits), ([], []))
        self.assertIn('$11:"source.js":', raw)
        self.assertIn("!11", raw)

    def test_native_call_without_a_script_uses_standard_unknown_context(self):
        raw, notices, limits, _ = self.capture("no-script")
        self.assertEqual((notices, limits), ([], []))
        self.assertIn("!?", raw)
        self.assertTrue(any(item.startswith("g-1:") for item in raw))

    def test_source_inspection_preserves_the_callers_pending_exception(self):
        raw, notices, limits, state = self.capture("preexisting-exception")
        self.assertEqual(limits, [])
        self.assertEqual(len(notices), 1)
        self.assertTrue(any(item.startswith("g-1:") for item in raw))
        self.assertIn("pending=1;oom=0", state)

    def test_real_source_hook_retrieves_source_and_retains_full_text(self):
        for scenario in ("retrievable", "retrievable-utf8"):
            with self.subTest(scenario=scenario):
                raw, notices, limits, state = self.capture(scenario)
                self.assertEqual((notices, limits), ([], []))
                self.assertIn("$11:\"source.js\":retrieved source \\\\n", raw)
                self.assertIn("!11", raw)
                self.assertTrue(any(item.startswith("g2:") for item in raw))
                self.assertIn("hook_reads=1", state)

    def test_permission_unavailable_retries_without_fake_empty_source(self):
        raw, notices, limits, state = self.capture("retry")
        self.assertEqual(limits, [])
        self.assertEqual(len(notices), 1)
        self.assertEqual((notices[0]["reason"], notices[0]["retrievable"]), ("source_not_loaded", True))
        self.assertTrue(any(item.startswith("g-1:") for item in raw))
        self.assertTrue(any(item.startswith("$11:") and item.endswith("retrieved source \\\\n") for item in raw))
        self.assertEqual(sum(item.startswith("g2:") for item in raw), 2)
        self.assertIn("hook_reads=1", state)

    def test_unavailable_ancestor_keeps_complete_child_with_unknown_eval_parent(self):
        raw, notices, limits, _ = self.capture("missing-parent")
        self.assertEqual(limits, [])
        self.assertEqual([(item["source_id"], item["reason"]) for item in notices], [(10, "source_missing")])
        self.assertIn("$11:#?:full source \\\\n", raw)
        self.assertFalse(any(item.startswith("$10:") for item in raw))
        self.assertTrue(any(item.startswith("g2:") for item in raw))

    def test_real_debug_metadata_can_link_same_source_and_cycle_is_explicitly_cut(self):
        raw, notices, limits, _ = self.capture("self-introduction")
        self.assertEqual(limits, [])
        self.assertEqual([(item["reason"], item["scope"], item["introduction_source_id"]) for item in notices],
                         [("introduction_cycle", "introduction", 11)])
        self.assertIn("$11:#?:full source \\\\n", raw)
        self.assertTrue(any(item.startswith("g2:") for item in raw))

    def test_two_source_cycle_cuts_only_the_closing_introduction_edge(self):
        raw, notices, limits, _ = self.capture("two-introductions")
        self.assertEqual(limits, [])
        self.assertEqual([(item["source_id"], item["introduction_source_id"]) for item in notices], [(10, 11)])
        self.assertIn("$10:#?:ancestor source", raw)
        self.assertIn("$11:10:full source \\\\n", raw)
        self.assertTrue(any(item.startswith("g2:") for item in raw))

    def test_context_callback_errors_are_loss_instead_of_unknown_origin(self):
        for scenario in ("context-error", "context-oom"):
            with self.subTest(scenario=scenario):
                raw, notices, limits, state = self.capture(scenario)
                self.assertEqual(notices, [])
                self.assertEqual(len(limits), 1)
                self.assertEqual(limits[0]["reason"], scenario.replace("context-", "engine_context_"))
                self.assertFalse(any(item.startswith("g") for item in raw))
                self.assertIn("pending=0;oom=0", state)

    def test_real_errors_and_oom_stop_capture_with_source_diagnostics(self):
        for scenario, stage in (("load-error", "try_load_source"), ("load-oom", "try_load_source"),
                                ("substring-oom", "substring"), ("linear-oom", "ensure_linear"),
                                ("append-oom", "pending_append"), ("filename-error", "filename")):
            with self.subTest(scenario=scenario):
                raw, notices, limits, state = self.capture(scenario)
                self.assertEqual(notices, [])
                self.assertEqual(len(limits), 1)
                self.assertEqual((limits[0]["source_id"], limits[0]["stage"]), (11, stage))
                self.assertEqual(limits[0]["filename"], "source.js")
                self.assertTrue(limits[0]["reason"].endswith("_oom" if "oom" in scenario else "_error"))
                self.assertFalse(any(item.startswith("g") for item in raw))
                self.assertIn("pending=0;oom=0", state)

    def test_unavailable_metadata_is_bounded_and_explicitly_truncated(self):
        raw, notices, limits, _ = self.capture("long-missing")
        self.assertEqual(limits, [])
        self.assertEqual(len(notices), 1)
        self.assertTrue(notices[0]["truncated"])
        self.assertTrue(notices[0]["filename_truncated"])
        self.assertTrue(notices[0]["introduction_type_truncated"])
        self.assertTrue(any(item.startswith("g-1:") for item in raw))


if __name__ == "__main__":
    unittest.main()
