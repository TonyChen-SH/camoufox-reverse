/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this file,
 * You can obtain one at https://mozilla.org/MPL/2.0/. */

#include "CamoufoxTrace.h"
#include "CamoufoxTraceBuffer.h"

#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <new>
#include <string_view>
#include <tuple>

#ifdef XP_WIN
#  include <process.h>
#else
#  include <unistd.h>
#endif

#include "jsapi.h"
#include "jsfriendapi.h"
#include "js/Class.h"
#include "js/ColumnNumber.h"
#include "js/Exception.h"
#include "js/GlobalObject.h"
#include "js/Object.h"
#include "js/RootingAPI.h"
#include "js/Realm.h"
#include "js/Stack.h"
#include "js/String.h"
#include "mozilla/Span.h"

namespace mozilla::camoutrace {
namespace {

struct Config {
  bool enabled = false;
  bool stack = false;
  uint64_t maxEvents = 10000;
  bool valid = true;
};

const Config& Configuration() {
  static const Config config = [] {
    Config result;
    const char* enabled = std::getenv("CAMOUFOX_TRACE");
    result.enabled = enabled && std::string_view(enabled) == "1";
    const char* stack = std::getenv("CAMOUFOX_TRACE_STACK");
    result.stack = stack && std::string_view(stack) == "1";
    const char* maximum = std::getenv("CAMOUFOX_TRACE_MAX_EVENTS");
    if (maximum) {
      uint64_t parsed = 0;
      auto end = maximum + std::strlen(maximum);
      auto conversion = std::from_chars(maximum, end, parsed);
      if (conversion.ec == std::errc() && conversion.ptr == end &&
          parsed > 0 && parsed <= 1000000) {
        result.maxEvents = parsed;
      } else {
        result.valid = false;
        result.maxEvents = 0;
      }
    }
    return result;
  }();
  return config;
}

uint64_t ProcessId() {
#ifdef XP_WIN
  return static_cast<uint64_t>(_getpid());
#else
  return static_cast<uint64_t>(getpid());
#endif
}

uint64_t Now() {
  return std::chrono::duration_cast<std::chrono::microseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count();
}

std::atomic<uint64_t> gSequence{0};
std::atomic<uint64_t> gEmitted{0};
std::atomic<bool> gSaturated{false};
std::once_flag gMetadata;
std::mutex gOutput;
thread_local bool gRecording = false;

class Recording final {
 public:
  Recording() { gRecording = true; }
  ~Recording() { gRecording = false; }
};

class PreserveException final {
 public:
  explicit PreserveException(JSContext* cx) : mSaved(cx) {}
  ~PreserveException() { mSaved.restore(); }

 private:
  JS::AutoSaveExceptionState mSaved;
};

bool Write(std::string_view record) {
  const int previousErrno = errno;
  std::lock_guard<std::mutex> lock(gOutput);
  const bool written = std::fwrite(record.data(), 1, record.size(), stderr) ==
                       record.size();
  if (!written) gSaturated.store(true);
  errno = previousErrno;
  return written;
}

void Limit(uint64_t emitted, const char* reason) {
  JsonBuffer<256> record;
  record.Raw("CAMOUFOX_TRACE {\"schema\":1,\"event\":\"limit\",\"pid\":");
  record.UInt(ProcessId());
  record.Raw(",\"emitted\":"); record.UInt(emitted);
  record.Raw(",\"reason\":"); record.String(reason);
  record.Raw("}\n");
  Write(record.View());
}

void Metadata() {
  JsonBuffer<512> record;
  record.Raw("CAMOUFOX_TRACE {\"schema\":1,\"event\":\"meta\",\"pid\":");
  record.UInt(ProcessId());
  record.Raw(",\"transport\":\"stderr\",\"coverage\":\"binding-pilot\","
             "\"producer\":\"camoufox-reverse-pilot\",\"firefox\":\"158.0.1\","
             "\"camoufox_commit\":\"b6205b0ee1e4c2e8e4bb9eb0e084472028a9c6a3\","
             "\"source_column\":\"one-origin\",\"max_events\":");
  record.UInt(Configuration().maxEvents);
  record.Raw("}\n");
  Write(record.View());
}

template <size_t Size>
bool String(JSContext* cx, JSString* string, JsonBuffer<Size>& output,
            size_t limit) {
  char encoded[768];
  auto result = JS_EncodeStringToUTF8BufferPartial(
      cx, string, mozilla::Span<char>(encoded, sizeof(encoded)));
  if (!result) {
    output.String("<encoding-unavailable>");
    output.MarkTruncated();
    return true;
  }
  auto [read, written] = *result;
  bool truncated = output.String(std::string_view(encoded, written), limit);
  if (read < JS::GetStringLength(string)) {
    output.MarkTruncated();
    truncated = true;
  }
  return truncated;
}

template <size_t Size>
void Value(JSContext* cx, JS::HandleValue value, JsonBuffer<Size>& output) {
  if (value.isString()) {
    output.Raw("{\"type\":\"string\",\"value\":");
    if (String(cx, value.toString(), output, 128)) {
      output.Raw(",\"truncated\":true");
    }
    output.Raw("}");
  } else if (value.isNumber()) {
    output.Raw("{\"type\":\"number\",\"value\":");
    output.Number(value.toNumber()); output.Raw("}");
  } else if (value.isBoolean()) {
    output.Raw(value.toBoolean() ? "{\"type\":\"boolean\",\"value\":true}"
                                : "{\"type\":\"boolean\",\"value\":false}");
  } else if (value.isNull()) {
    output.Raw("{\"type\":\"null\"}");
  } else if (value.isUndefined()) {
    output.Raw("{\"type\":\"undefined\"}");
  } else if (value.isObject()) {
    output.Raw("{\"type\":\"object\",\"class\":");
    output.String(JS::GetClass(&value.toObject())->name, 64);
    output.Raw("}");
  } else {
    output.Raw("{\"type\":\"unavailable\",\"reason\":\"unsupported_type\"}");
  }
}

}  // namespace

struct State {
  JSContext* cx;
  JS::Value* returnValue;
  const char* api;
  const char* operation;
  uint64_t sequence;
  uint64_t started;
  JsonBuffer<1024> source;
  JsonBuffer<1024> arguments;
  JsonBuffer<768> stack;
  const char* stackStatus = "disabled";
  const char* realm = nullptr;
};

static State* Begin(JSContext* cx, JS::Value* returnValue, const char* api,
                    const char* operation, const JSJitMethodCallArgs* args) {
  if (!Configuration().enabled || gRecording || gSaturated.load()) return nullptr;
  Recording recording;
  const int previousErrno = errno;
  PreserveException saved(cx);
  std::call_once(gMetadata, Metadata);
  if (gSaturated.load()) {
    errno = previousErrno;
    return nullptr;
  }
  if (!Configuration().valid) {
    if (!gSaturated.exchange(true)) Limit(0, "invalid_max_events");
    errno = previousErrno;
    return nullptr;
  }
  uint64_t sequence = gSequence.fetch_add(1);
  if (sequence >= Configuration().maxEvents) {
    if (!gSaturated.exchange(true)) Limit(gEmitted.load(), "event_cap");
    errno = previousErrno;
    return nullptr;
  }
  State* state = new (std::nothrow) State;
  if (!state) {
    gSaturated.store(true);
    Limit(gEmitted.load(), "allocation");
    errno = previousErrno;
    return nullptr;
  }
  state->cx = cx; state->returnValue = returnValue;
  state->api = api; state->operation = operation;
  state->sequence = sequence + 1; state->started = Now();
  JS::AutoFilename filename;
  uint32_t line = 0;
  JS::ColumnNumberOneOrigin column;
  state->source.Raw("{\"url\":");
  if (JS::DescribeScriptedCaller(&filename, cx, &line, &column)) {
    if (filename.get()) state->source.String(filename.get(), 384);
    else state->source.Raw("null");
    state->source.Raw(",\"line\":"); state->source.UInt(line);
    state->source.Raw(",\"column\":"); state->source.UInt(column.oneOriginValue());
  } else {
    state->source.Raw("null,\"line\":null,\"column\":null");
  }
  state->source.Raw("}");
  if (JSObject* global = JS::CurrentGlobalOrNull(cx)) {
    state->realm = JS::GetClass(global)->name;
  }
  state->arguments.Raw("[");
  unsigned length = args ? args->length() : 0;
  for (unsigned i = 0; i < length && i < 4; ++i) {
    if (i) state->arguments.Raw(",");
    JS::RootedValue argument(cx, args->get(i));
    Value(cx, argument, state->arguments);
  }
  if (length > 4) state->arguments.MarkTruncated();
  state->arguments.Raw("]");
  JS::RootedObject frames(cx);
  JS::RootedString stack(cx);
  if (Configuration().stack) {
    JS::Realm* realm = JS::GetCurrentRealmOrNull(cx);
    if (JS::CaptureCurrentStack(cx, &frames,
                                JS::StackCapture(JS::MaxFrames(12))) &&
        realm && JS::BuildStackString(cx, JS::GetRealmPrincipals(realm),
                                      frames, &stack)) {
      String(cx, stack, state->stack, 512);
      state->stackStatus = "captured";
    } else {
      state->stack.Raw("null");
      state->stackStatus = "unavailable";
    }
  } else {
    state->stack.Raw("null");
  }
  errno = previousErrno;
  return state;
}

static void Finish(State* state) {
  if (!state) return;
  Recording recording;
  const int previousErrno = errno;
  const bool threw = JS_IsExceptionPending(state->cx);
  PreserveException saved(state->cx);
  JsonBuffer<4096> record;
  record.Raw("CAMOUFOX_TRACE {\"schema\":1,\"event\":\"api\",\"pid\":");
  record.UInt(ProcessId());
  record.Raw(",\"seq\":"); record.UInt(state->sequence);
  record.Raw(",\"mono_us\":"); record.UInt(state->started);
  record.Raw(",\"duration_us\":"); record.UInt(Now() - state->started);
  record.Raw(",\"api\":"); record.String(state->api, 96);
  record.Raw(",\"op\":"); record.String(state->operation, 8);
  record.Raw(",\"source\":"); record.Raw(state->source.View());
  record.Raw(",\"realm_class\":");
  if (state->realm) record.String(state->realm, 64);
  else record.Raw("null");
  record.Raw(",\"args\":"); record.Raw(state->arguments.View());
  record.Raw(",\"result\":");
  if (threw) {
    record.Raw("{\"type\":\"unavailable\",\"reason\":\"exception\"}");
  } else {
    JS::RootedValue result(state->cx, *state->returnValue);
    Value(state->cx, result, record);
  }
  record.Raw(threw ? ",\"threw\":true" : ",\"threw\":false");
  record.Raw(",\"stack\":"); record.Raw(state->stack.View());
  record.Raw(",\"stack_status\":"); record.String(state->stackStatus, 16);
  bool truncated = record.Truncated() || state->source.Truncated() ||
                   state->arguments.Truncated() || state->stack.Truncated();
  record.Raw(truncated ? ",\"truncated\":true}\n" : ",\"truncated\":false}\n");
  if (record.Failed() || state->source.Failed() ||
      state->arguments.Failed() || state->stack.Failed()) {
    gSaturated.store(true);
    Limit(gEmitted.load(), "record_overflow");
  } else {
    if (Write(record.View())) gEmitted.fetch_add(1);
  }
  delete state;
  errno = previousErrno;
}

AutoTraceMethod::AutoTraceMethod(JSContext* cx, const JSJitMethodCallArgs& args,
                                 const char* name)
    : mState(Begin(cx, args.rval().address(), name, "call", &args)) {}
AutoTraceMethod::~AutoTraceMethod() { Finish(mState); }
AutoTraceGetter::AutoTraceGetter(JSContext* cx, JSJitGetterCallArgs args,
                                 const char* name)
    : mState(Begin(cx, args.rval().address(), name, "get", nullptr)) {}
AutoTraceGetter::~AutoTraceGetter() { Finish(mState); }

}  // namespace mozilla::camoutrace
