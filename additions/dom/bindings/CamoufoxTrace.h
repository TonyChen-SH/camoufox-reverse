/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this file,
 * You can obtain one at https://mozilla.org/MPL/2.0/. */

#ifndef mozilla_dom_CamoufoxTrace_h
#define mozilla_dom_CamoufoxTrace_h

#include "mozilla/Attributes.h"
#include "js/experimental/JitInfo.h"

namespace mozilla::camoutrace {

struct State;

class MOZ_RAII AutoTraceMethod final {
 public:
  AutoTraceMethod(JSContext* cx, const JSJitMethodCallArgs& args,
                  const char* interface, const char* member);
  ~AutoTraceMethod();
  AutoTraceMethod(const AutoTraceMethod&) = delete;
  AutoTraceMethod& operator=(const AutoTraceMethod&) = delete;

 private:
  State* mState;
};

class MOZ_RAII AutoTraceGetter final {
 public:
  AutoTraceGetter(JSContext* cx, JSJitGetterCallArgs args,
                  const char* interface, const char* member);
  ~AutoTraceGetter();
  AutoTraceGetter(const AutoTraceGetter&) = delete;
  AutoTraceGetter& operator=(const AutoTraceGetter&) = delete;

 private:
  State* mState;
};

class MOZ_RAII AutoTraceSetter final {
 public:
  AutoTraceSetter(JSContext* cx, JSJitSetterCallArgs args,
                  const char* interface, const char* member);
  ~AutoTraceSetter();
  AutoTraceSetter(const AutoTraceSetter&) = delete;
  AutoTraceSetter& operator=(const AutoTraceSetter&) = delete;

 private:
  State* mState;
};

class MOZ_RAII AutoTraceNative final {
 public:
  AutoTraceNative(JSContext* cx, unsigned argc, JS::Value* vp,
                  const char* interface, const char* member,
                  const char* operation, const char* scope);
  ~AutoTraceNative();
  AutoTraceNative(const AutoTraceNative&) = delete;
  AutoTraceNative& operator=(const AutoTraceNative&) = delete;

 private:
  State* mState;
};

}  // namespace mozilla::camoutrace
#endif
