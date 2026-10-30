/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/. */

#ifndef vm_CamoufoxMonitor_h
#define vm_CamoufoxMonitor_h

#include <cstdint>

#include "js/CallArgs.h"
#include "js/RootingAPI.h"

namespace js {

bool CamoufoxMonitorEnabled();
void CamoufoxMonitorFailure(const char* reason);
void CamoufoxMonitorProperty(JSContext* cx, uint32_t operation,
                             JS::HandleValue receiver, JS::HandleValue key,
                             JS::HandleValue assigned);
void CamoufoxMonitorNative(JSContext* cx, const JS::CallArgs& args);
void CamoufoxMonitorDirectEval(JSContext* cx, JS::HandleValue source);
void CamoufoxMonitorDynamicCall(JSContext* cx, const JS::CallArgs& args,
                                bool undefinedReceiver);

}  // namespace js
#endif
