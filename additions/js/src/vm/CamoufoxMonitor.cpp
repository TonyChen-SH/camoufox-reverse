/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/. */

#include "vm/CamoufoxMonitor.h"
#include "vm/CamoufoxMonitorBuffer.h"

#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <mutex>
#include <string>
#include <string_view>
#include <unordered_set>
#include <utility>
#include <vector>

#include "jsfriendapi.h"

#ifdef XP_WIN
#  include <process.h>
#else
#  include <unistd.h>
#endif

#include "js/CallAndConstruct.h"
#include "js/CharacterEncoding.h"
#include "js/Exception.h"
#include "js/GCVector.h"
#include "js/PropertyDescriptor.h"
#include "js/Proxy.h"
#include "js/String.h"
#include "js/Wrapper.h"
#include "js/friend/WindowProxy.h"
#include "proxy/DOMProxy.h"
#include "vm/ArrayObject.h"
#include "vm/FrameIter.h"
#include "vm/GlobalObject.h"
#include "vm/Iteration.h"
#include "vm/JSContext.h"
#include "vm/JSFunction.h"
#include "vm/JSScript.h"
#include "vm/PlainObject.h"
#include "vm/ProxyObject.h"
#include "vm/Realm.h"
#include "vm/RegExpObject.h"
#include "vm/StringType.h"
#include "vm/SymbolType.h"

#include "gc/StableCellHasher-inl.h"
#include "vm/JSFunction-inl.h"
#include "vm/JSObject-inl.h"
#include "vm/NativeObject-inl.h"
#include "vm/ObjectOperations-inl.h"
#include "vm/StringType-inl.h"

using js::camoufox::Vv8Record;

namespace {

struct Config {
  bool enabled = false;
  bool privileged = false;
  bool valid = true;
  uint64_t maximum = 0;
};

const Config& Configuration() {
  static const Config config = [] {
    Config result;
    const char* enabled = std::getenv("CAMOUFOX_ENGINE_TRACE");
    result.enabled = enabled && std::string_view(enabled) == "1";
    const char* privileged = std::getenv("CAMOUFOX_ENGINE_PRIVILEGED");
    result.privileged = privileged && std::string_view(privileged) == "1";
    if (const char* maximum = std::getenv("CAMOUFOX_ENGINE_MAX_RECORDS")) {
      auto end = maximum + std::char_traits<char>::length(maximum);
      auto parsed = std::from_chars(maximum, end, result.maximum);
      result.valid = parsed.ec == std::errc() && parsed.ptr == end;
    }
    return result;
  }();
  return config;
}

std::atomic<JS::CamoufoxMonitorContextCallback> gContextCallback{nullptr};
std::mutex gNativeMutex;
std::unordered_set<JS::CamoufoxHostNative> gHostNatives;
std::unordered_set<const JSClass*> gHostClasses;
std::mutex gOutputMutex;
std::atomic<uint64_t> gNextThread{1};
std::atomic<uint64_t> gRecords{0};
std::atomic<uint64_t> gEmitted{0};
std::atomic<bool> gStopped{false};
thread_local bool gRecording = false;

uint64_t ProcessId() {
#ifdef XP_WIN
  return static_cast<uint64_t>(_getpid());
#else
  return static_cast<uint64_t>(getpid());
#endif
}

void Limit(const char* reason) {
  if (gStopped.exchange(true)) return;
  mozilla::camoutrace::JsonBuffer<512> record;
  record.Raw("CAMOUFOX_TRACE {\"schema\":1,\"event\":\"limit\",\"pid\":");
  record.UInt(ProcessId());
  record.Raw(",\"channel\":\"engine\",\"emitted\":");
  record.UInt(gEmitted.load());
  record.Raw(",\"reason\":"); record.String(reason);
  record.Raw("}\n");
  std::lock_guard<std::mutex> lock(gOutputMutex);
  auto text = record.View();
  (void)std::fwrite(text.data(), 1, text.size(), stderr);
}

using SourceKey = std::pair<uintptr_t, uint32_t>;
struct SourceInfo {
  uint32_t firstLine;
  uint32_t firstColumn;
  uint64_t length;
  std::vector<uint64_t> lineStarts;
};

struct ThreadState {
  uint64_t tid = gNextThread.fetch_add(1);
  uint64_t sequence = 0;
  JSRuntime* runtime = nullptr;
  int64_t script = -1;
  std::string origin;
  std::map<SourceKey, SourceInfo> sources;
};
thread_local ThreadState gThread;

class Recording final {
 public:
  explicit Recording(JSContext* cx)
      : mErrno(errno), mExceptions(cx) {
    gRecording = true;
  }
  ~Recording() {
    mExceptions.restore();
    gRecording = false;
    errno = mErrno;
  }

 private:
  int mErrno;
  JS::AutoSaveExceptionState mExceptions;
};

bool CanRecord(JSContext* cx) {
  return Configuration().enabled && !gRecording && !gStopped.load() &&
         cx->realm() &&
         (Configuration().privileged || !cx->realm()->isSystem());
}

bool Emit(std::string_view raw) {
  if (gStopped.load()) return false;
  if (!Configuration().valid) {
    Limit("invalid_engine_max_records");
    return false;
  }
  uint64_t count = gRecords.fetch_add(1);
  if (Configuration().maximum && count >= Configuration().maximum) {
    Limit("engine_record_cap");
    return false;
  }
  bool written;
  {
    std::lock_guard<std::mutex> lock(gOutputMutex);
    written = js::camoufox::FrameVv8Record(
        raw, ProcessId(), gThread.tid, ++gThread.sequence,
        [](std::string_view text) {
          return std::fwrite(text.data(), 1, text.size(), stderr) == text.size();
        });
  }
  if (!written) Limit("engine_transport");
  else gEmitted.fetch_add(1);
  return written;
}

bool AppendString(JSContext* cx, JS::HandleString string,
                   Vv8Record& record, bool quotes = true) {
  JSLinearString* linear = string->ensureLinear(cx);
  if (!linear) return false;
  JS::AutoCheckCannotGC nogc;
  if (quotes) record.Raw("\"");
  if (linear->hasLatin1Chars()) {
    const auto* chars = linear->latin1Chars(nogc);
    for (size_t i = 0; i < linear->length(); ++i) record.Unit(chars[i]);
  } else {
    const auto* chars = linear->twoByteChars(nogc);
    for (size_t i = 0; i < linear->length(); ++i) record.Unit(chars[i]);
  }
  if (quotes) record.Raw("\"");
  return true;
}

bool DirectHostObject(JSObject* object) {
  const JSClass* clasp = object->getClass();
  if (object->is<js::GlobalObject>() || clasp->isDOMClass() ||
      js::IsWindowProxy(object)) {
    return true;
  }
  if (!object->is<js::ProxyObject>()) {
    std::lock_guard<std::mutex> lock(gNativeMutex);
    return gHostClasses.count(clasp);
  }
  const void* family = object->as<js::ProxyObject>().handler()->family();
  return family == js::GetDOMProxyHandlerFamily() ||
         family == js::GetDOMRemoteProxyHandlerFamily();
}

JSObject* HostTarget(JSObject* object) {
  // Resolve native wrappers for metadata without invoking property traps.
  JSObject* target = js::IsWrapper(object)
                         ? js::UncheckedUnwrapWithoutExpose(object)
                         : object;
  return target && DirectHostObject(target) ? target : nullptr;
}

bool HostObject(JS::HandleValue value) {
  return value.isObject() && HostTarget(&value.toObject());
}

bool AppendValue(JSContext* cx, JS::HandleValue value, Vv8Record& record,
                  bool expand = false);

bool AppendClass(JSContext* cx, JS::HandleObject object, Vv8Record& record,
                  bool* plainConstructor) {
  JS::RootedObject classObject(cx, HostTarget(object));
  if (!classObject) classObject = object;
  const JSClass* clasp = classObject->getClass();
  if (js::IsWindowProxy(classObject)) {
    clasp = js::ToWindowIfWindowProxy(classObject)->getClass();
  }
  const char* fallback = clasp->name;
  if (classObject->is<js::ProxyObject>() &&
      classObject->as<js::ProxyObject>().handler()->family() ==
          js::GetDOMRemoteProxyHandlerFamily()) {
    fallback = classObject->as<js::ProxyObject>().handler()->className(
        cx, classObject);
  }
  // Descriptor inspection and expansion stay on the original object.
  *plainConstructor = object->is<js::PlainObject>() ||
                      object->is<js::ArrayObject>();
  if (*plainConstructor) {
    JS::RootedObject prototype(cx, object->staticPrototype());
    while (prototype && prototype->is<js::NativeObject>() &&
           !prototype->getClass()->getResolve()) {
      JS::RootedId id(cx, js::NameToId(cx->names().constructor));
      JS::Rooted<mozilla::Maybe<JS::PropertyDescriptor>> desc(cx);
      if (!js::GetOwnPropertyDescriptor(cx, prototype, id, &desc)) return false;
      if (desc.isSome()) {
        if (desc->isDataDescriptor() && desc->value().isObject() &&
            desc->value().toObject().is<JSFunction>()) {
          JS::RootedFunction constructor(cx, &desc->value().toObject().as<JSFunction>());
          JS::Rooted<JSAtom*> name(cx);
          if (!constructor->getDisplayAtom(cx, &name)) return false;
          if (name && name->length()) {
            JS::RootedString text(cx, name);
            if (!AppendString(cx, text, record, false)) return false;
            *plainConstructor = record.View() == "Object" || record.View() == "Array";
            return true;
          }
        }
        break;
      }
      prototype = prototype->staticPrototype();
    }
  }
  record.Ascii(fallback);
  return true;
}

bool AppendObject(JSContext* cx, JS::HandleObject object,
                   Vv8Record& record, bool expand) {
  uint64_t identity;
  if (!js::gc::GetOrCreateUniqueId(object, &identity)) return false;
  Vv8Record constructor;
  bool plainConstructor = false;
  if (!AppendClass(cx, object, constructor, &plainConstructor)) return false;
  record.Raw("{"); record.Unsigned(identity);
  if (expand && plainConstructor) {
    JS::RootedIdVector keys(cx);
    if (!js::GetPropertyKeys(cx, object, JSITER_OWNONLY, &keys)) return false;
    for (size_t i = 0; i < keys.length(); ++i) {
      JS::RootedId id(cx, keys[i]);
      if (id.isSymbol()) continue;
      record.Raw(",");
      if (id.isInt()) {
        record.Signed(id.toInt());
      } else {
        JS::RootedString key(cx, id.toAtom());
        if (!AppendString(cx, key, record, false)) return false;
      }
      record.Raw("\\:");
      JS::Rooted<mozilla::Maybe<JS::PropertyDescriptor>> desc(cx);
      if (!js::GetOwnPropertyDescriptor(cx, object, id, &desc)) return false;
      JS::RootedValue item(cx, JS::UndefinedValue());
      if (desc.isSome() && desc->isDataDescriptor()) item = desc->value();
      if (!AppendValue(cx, item, record)) return false;
    }
  } else {
    record.Raw(","); record.Raw(constructor.View());
  }
  record.Raw("}");
  return true;
}

bool AppendValue(JSContext* cx, JS::HandleValue value, Vv8Record& record,
                  bool expand) {
  if (value.isString()) {
    JS::RootedString string(cx, value.toString());
    return AppendString(cx, string, record);
  }
  if (value.isInt32()) {
    record.Signed(value.toInt32());
  } else if (value.isNumber()) {
    record.Number(value.toNumber());
  } else if (value.isUndefined()) {
    record.Raw("#U");
  } else if (value.isNull()) {
    record.Raw("#N");
  } else if (value.isBoolean()) {
    record.Raw(value.toBoolean() ? "#T" : "#F");
  } else if (value.isSymbol()) {
    JS::RootedString description(cx, value.toSymbol()->description());
    if (description) return AppendString(cx, description, record, false);
    record.Raw("?");
  } else if (value.isObject()) {
    JS::RootedObject object(cx, &value.toObject());
    if (object->is<JSFunction>()) {
      JS::RootedFunction function(cx, &object->as<JSFunction>());
      if (!function->isInterpreted() || function->isSelfHostedBuiltin()) record.Raw("%");
      JS::Rooted<JSAtom*> name(cx);
      if (!function->getDisplayAtom(cx, &name)) return false;
      if (name && name->length()) {
        JS::RootedString string(cx, name);
        return AppendString(cx, string, record, false);
      }
      record.Raw("<anonymous>");
    } else if (object->is<js::RegExpObject>()) {
      JS::RootedString pattern(cx, object->as<js::RegExpObject>().getSource());
      record.Raw("/");
      if (!AppendString(cx, pattern, record, false)) return false;
      record.Raw("/");
    } else {
      return AppendObject(cx, object, record, expand);
    }
  } else {
    record.Raw("?");
  }
  return true;
}

SourceKey Key(JSRuntime* runtime, uint32_t source) {
  return {reinterpret_cast<uintptr_t>(runtime), source};
}

bool RegisterSource(JSContext* cx, JS::HandleObject current) {
  JS::RootedObjectVector pending(cx);
  JS::RootedObject sourceObject(cx, current);
  std::unordered_set<uint32_t> pendingIds;
  while (sourceObject) {
    auto* source = sourceObject->as<js::ScriptSourceObject>().source();
    if (gThread.sources.count(Key(cx->runtime(), source->id()))) break;
    if (!pendingIds.insert(source->id()).second) return false;
    if (!pending.append(sourceObject)) return false;
    js::BaseScript* parent = sourceObject->as<js::ScriptSourceObject>()
                                  .unwrappedIntroductionScript();
    sourceObject = parent ? parent->sourceObject() : nullptr;
  }
  for (size_t i = pending.length(); i > 0; --i) {
    sourceObject = pending[i - 1];
    auto* source = sourceObject->as<js::ScriptSourceObject>().source();
    uint32_t sourceId = source->id();
    JS::RootedString text(cx);
    {
      js::ScriptSource::DataReader reader(source);
      if (!reader.hasSourceText()) return false;
      text = reader->substring(cx, 0, reader->length());
    }
    if (!text) return false;
    SourceInfo info{source->startLine(), source->startColumn().oneOriginValue(),
                    text->length(), {0}};
    JSLinearString* linear = text->ensureLinear(cx);
    if (!linear) return false;
    {
      JS::AutoCheckCannotGC nogc;
      auto unit = [&](size_t index) -> char16_t {
        return linear->hasLatin1Chars() ? linear->latin1Chars(nogc)[index]
                                        : linear->twoByteChars(nogc)[index];
      };
      for (size_t n = 0; n < linear->length(); ++n) {
        char16_t c = unit(n);
        if (c == '\r' && n + 1 < linear->length() && unit(n + 1) == '\n') ++n;
        if (c == '\r' || c == '\n' || c == 0x2028 || c == 0x2029) {
          info.lineStarts.push_back(n + 1);
        }
      }
    }
    Vv8Record registration;
    registration.Raw("$"); registration.Unsigned(sourceId); registration.Raw(":");
    js::BaseScript* parent = sourceObject->as<js::ScriptSourceObject>()
                                  .unwrappedIntroductionScript();
    if (parent) {
      registration.Unsigned(parent->scriptSource()->id());
    } else if (source->hasIntroductionType() &&
               (std::string_view(source->introductionType()).find("eval") != std::string_view::npos ||
                std::string_view(source->introductionType()).find("Function") != std::string_view::npos)) {
      registration.Raw("#?");
    } else if (source->filename()) {
      JS::RootedString filename(cx, JS_NewStringCopyUTF8Z(cx, JS::ConstUTF8CharsZ(source->filename())));
      if (!filename || !AppendString(cx, filename, registration)) return false;
    } else {
      registration.Raw("#U");
    }
    registration.Raw(":");
    if (!AppendString(cx, text, registration, false) || !Emit(registration.View())) return false;
    gThread.sources.emplace(Key(cx->runtime(), sourceId), std::move(info));
  }
  return true;
}

bool Context(JSContext* cx, int64_t* offset) {
  *offset = -1;
  if (gThread.runtime != cx->runtime()) {
    gThread.runtime = cx->runtime();
    gThread.script = -1;
    gThread.origin.clear();
    Vv8Record isolate;
    isolate.Raw("~0x");
    isolate.Unsigned(reinterpret_cast<uintptr_t>(cx->runtime()), 16);
    if (!Emit(isolate.View())) return false;
  }
  Vv8Record origin;
  JS::RootedString href(cx), token(cx);
  auto callback = gContextCallback.load();
  if (callback && callback(cx, &href, &token) && href && token) {
    origin.Raw("@");
    if (!AppendString(cx, href, origin)) return false;
    origin.Raw(":");
    if (!AppendString(cx, token, origin)) return false;
  } else {
    cx->clearPendingException();
    origin.Raw("@?");
  }
  if (origin.View() != gThread.origin) {
    if (!Emit(origin.View())) return false;
    gThread.origin.assign(origin.View());
  }
  js::NonBuiltinScriptFrameIter frame(cx);
  if (frame.done()) {
    gThread.script = -1;
    return Emit("!?");
  }
  JS::RootedScript script(cx, frame.script());
  jsbytecode* pc = frame.pc();
  JS::LimitedColumnNumberOneOrigin column;
  unsigned line = js::PCToLineNumber(script, pc, &column);
  JS::RootedObject sourceObject(cx, script->sourceObject());
  uint32_t sourceId = script->scriptSource()->id();
  if (!gThread.sources.count(Key(cx->runtime(), sourceId)) &&
      !RegisterSource(cx, sourceObject)) return false;
  const SourceInfo& info = gThread.sources.at(Key(cx->runtime(), sourceId));
  if (line >= info.firstLine) {
    uint64_t lineIndex = line - info.firstLine;
    uint64_t firstColumn = lineIndex ? 1 : info.firstColumn;
    if (lineIndex < info.lineStarts.size() && column.oneOriginValue() >= firstColumn) {
      uint64_t position = info.lineStarts[lineIndex] + column.oneOriginValue() - firstColumn;
      if (position <= info.length) *offset = static_cast<int64_t>(position);
    }
  }
  if (gThread.script != sourceId) {
    Vv8Record active;
    active.Raw("!"); active.Unsigned(sourceId);
    if (!Emit(active.View())) return false;
    gThread.script = sourceId;
  }
  return true;
}

void Call(JSContext* cx, const JS::CallArgs& args, bool dynamic,
            bool undefinedReceiver) {
  if (!CanRecord(cx)) return;
  Recording recording(cx);
  int64_t offset;
  if (!Context(cx, &offset)) {
    Limit("engine_source_or_context");
    return;
  }
  Vv8Record record;
  bool constructing = !dynamic && args.isConstructing();
  record.Raw(constructing ? "n" : "c"); record.Signed(offset); record.Raw(":");
  JS::RootedValue function(cx, args.calleev());
  if (!AppendValue(cx, function, record)) { Limit("engine_value"); return; }
  if (!constructing) {
    record.Raw(":");
    JS::RootedValue receiver(cx, undefinedReceiver ? JS::UndefinedValue() : args.thisv());
    if (!AppendValue(cx, receiver, record)) { Limit("engine_value"); return; }
  }
  for (unsigned i = 0; i < args.length(); ++i) {
    record.Raw(":");
    if (!AppendValue(cx, args[i], record, true)) { Limit("engine_value"); return; }
  }
  Emit(record.View());
}

}  // namespace

bool js::CamoufoxMonitorEnabled() { return Configuration().enabled; }

void js::CamoufoxMonitorFailure(const char* reason) {
  int saved = errno;
  Limit(reason);
  errno = saved;
}

void JS::RegisterCamoufoxHostNative(CamoufoxHostNative native) {
  if (!Configuration().enabled || !native) return;
  std::lock_guard<std::mutex> lock(gNativeMutex);
  gHostNatives.insert(native);
}

void JS::RegisterCamoufoxHostClass(const JSClass* clasp) {
  if (!Configuration().enabled || !clasp) return;
  std::lock_guard<std::mutex> lock(gNativeMutex);
  gHostClasses.insert(clasp);
}

void JS::SetCamoufoxMonitorContextCallback(CamoufoxMonitorContextCallback callback) {
  gContextCallback.store(callback);
}

void js::CamoufoxMonitorNative(JSContext* cx, const JS::CallArgs& args) {
  if (!CanRecord(cx)) return;
  bool observed = false;
  if (args.callee().is<JSFunction>()) {
    auto native = args.callee().as<JSFunction>().maybeNative();
    if (native) {
      std::lock_guard<std::mutex> lock(gNativeMutex);
      observed = gHostNatives.count(native);
    }
  } else {
    // A native wrapper forwards to another call boundary that records its target.
    observed = DirectHostObject(&args.callee());
  }
  if (observed) Call(cx, args, false, false);
}

void js::CamoufoxMonitorDynamicCall(JSContext* cx, const JS::CallArgs& args,
                                    bool undefinedReceiver) {
  Call(cx, args, true, undefinedReceiver);
}

void js::CamoufoxMonitorDirectEval(JSContext* cx, JS::HandleValue source) {
  if (!CanRecord(cx)) return;
  Recording recording(cx);
  int64_t offset;
  if (!Context(cx, &offset)) { Limit("engine_source_or_context"); return; }
  Vv8Record record;
  record.Raw("c"); record.Signed(offset); record.Raw(":%eval:#U:");
  if (!AppendValue(cx, source, record, true)) { Limit("engine_value"); return; }
  Emit(record.View());
}

void js::CamoufoxMonitorProperty(JSContext* cx, uint32_t operation,
                                 JS::HandleValue receiver, JS::HandleValue key,
                                 JS::HandleValue assigned) {
  if (!CanRecord(cx) || !HostObject(receiver)) return;
  Recording recording(cx);
  int64_t offset;
  if (!Context(cx, &offset)) { Limit("engine_source_or_context"); return; }
  Vv8Record record;
  record.Raw(operation == 1 ? "s" : "g"); record.Signed(offset); record.Raw(":");
  if (!AppendValue(cx, receiver, record)) { Limit("engine_value"); return; }
  record.Raw(":");
  if (!AppendValue(cx, key, record)) { Limit("engine_value"); return; }
  if (operation == 1) {
    record.Raw(":");
    if (!AppendValue(cx, assigned, record)) { Limit("engine_value"); return; }
  }
  Emit(record.View());
}
