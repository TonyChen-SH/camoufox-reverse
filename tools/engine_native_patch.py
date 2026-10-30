"""Native host-boundary, context and JIT changes for the pinned engine."""

PATHS = (
    "js/public/CallAndConstruct.h",
    "js/src/moz.build",
    "dom/bindings/BindingUtils.cpp",
    "js/src/vm/Interpreter.cpp",
    "js/src/jit/CacheIR.cpp",
    "js/src/jit/CodeGenerator.cpp",
    "js/src/builtin/Eval.cpp",
    "js/src/vm/JSFunction.cpp",
    "js/src/proxy/Proxy.cpp",
    "xpcom/base/CycleCollectedJSRuntime.cpp",
)


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Native engine anchor is missing or ambiguous; port the patch")
    return text.replace(old, new, 1)


def include_monitor(text, anchor):
    return replace_once(text, anchor, anchor + '#include "vm/CamoufoxMonitor.h"\n')


def transform(path, data):
    text = data.decode()
    if path == "js/public/CallAndConstruct.h":
        text = replace_once(text, 'struct JSContext;\n', 'struct JSContext;\nstruct JSClass;\n')
        anchor = 'extern JS_PUBLIC_API bool IsCallable(JSObject* obj);\n'
        declarations = '''
// Native embedding hooks for opt-in host activity capture. The registration
// uses code pointers, so it never retains an unrooted function object.
using CamoufoxHostNative = bool (*)(JSContext*, unsigned, Value*);
extern JS_PUBLIC_API void RegisterCamoufoxHostNative(CamoufoxHostNative native);
extern JS_PUBLIC_API void RegisterCamoufoxHostClass(const JSClass* clasp);
using CamoufoxMonitorContextCallback =
    bool (*)(JSContext*, MutableHandle<JSString*>, MutableHandle<JSString*>);
extern JS_PUBLIC_API void SetCamoufoxMonitorContextCallback(
    CamoufoxMonitorContextCallback callback);
'''
        text = replace_once(text, anchor, anchor + declarations)
    elif path == "js/src/moz.build":
        anchor = '    "../public/experimental/BindingAllocs.h",\n'
        text = replace_once(text, anchor, anchor + '    "../public/experimental/CamoufoxTraceBuffer.h",\n')
        anchor = '    "jsglean.cpp",\n'
        text = replace_once(text, anchor, anchor + '    "vm/CamoufoxMonitor.cpp",\n')
    elif path == "dom/bindings/BindingUtils.cpp":
        anchor = '#include "BindingUtils.h"\n'
        text = replace_once(text, anchor, anchor + '#include "js/CallAndConstruct.h"\n')
        anchor = '''static inline bool Define(JSContext* cx, JS::Handle<JSObject*> obj,
                          const JSFunctionSpec* spec) {
  return JS_DefineFunctions(cx, obj, spec);
}'''
        updated = '''static inline bool Define(JSContext* cx, JS::Handle<JSObject*> obj,
                          const JSFunctionSpec* spec) {
  for (const JSFunctionSpec* method = spec; method->name; ++method) {
    JS::RegisterCamoufoxHostNative(method->call.op);
  }
  return JS_DefineFunctions(cx, obj, spec);
}'''
        text = replace_once(text, anchor, updated)
        anchor = '''                          const JSPropertySpec* spec) {
  return JS_DefineProperties(cx, obj, spec);
}'''
        updated = '''                          const JSPropertySpec* spec) {
  for (const JSPropertySpec* attr = spec; attr->name; ++attr) {
    if (attr->isAccessor() && !attr->isSelfHosted()) {
      JS::RegisterCamoufoxHostNative(attr->u.accessors.getter.native.op);
      JS::RegisterCamoufoxHostNative(attr->u.accessors.setter.native.op);
    }
  }
  return JS_DefineProperties(cx, obj, spec);
}'''
        text = replace_once(text, anchor, updated)
        anchor = '''    JS::Handle<JSObject*> aProto) {
  JSFunction* fun = js::NewFunctionByIdWithReservedAndProto('''
        text = replace_once(text, anchor, '''    JS::Handle<JSObject*> aProto) {
  JS::RegisterCamoufoxHostNative(aNative);
  JSFunction* fun = js::NewFunctionByIdWithReservedAndProto(''')
        anchor = '''    bool isGlobal) {
  JS::Rooted<JSObject*> ourProto('''
        text = replace_once(text, anchor, '''    bool isGlobal) {
  JS::RegisterCamoufoxHostClass(protoClass);
  JS::Rooted<JSObject*> ourProto(''')
    elif path == "js/src/vm/Interpreter.cpp":
        text = include_monitor(text, '#include "vm/JSContext.h"\n')
        anchor = '  bool ok = native(cx, args.length(), args.base());\n'
        text = replace_once(text, anchor, '  CamoufoxMonitorNative(cx, args);\n' + anchor)
    elif path == "js/src/jit/CacheIR.cpp":
        text = include_monitor(text, '#include "vm/JSContext.h"\n') if '#include "vm/JSContext.h"\n' in text else include_monitor(text, '#include "vm/Iteration.h"\n')
        anchor = '''  RootedObject calleeObj(cx_, &callee_.toObject());
  if (calleeObj->is<BoundFunctionObject>()) {'''
        text = replace_once(text, anchor, '''  RootedObject calleeObj(cx_, &callee_.toObject());
  // Include call/apply, bound and class-hook specializations, which otherwise
  // bypass tryAttachCallNative and can call the host ABI directly.
  if (CamoufoxMonitorEnabled() &&
      (!calleeObj->is<JSFunction>() ||
       calleeObj->as<JSFunction>().isNativeWithoutJitEntry())) {
    return AttachDecision::NoAction;
  }
  if (calleeObj->is<BoundFunctionObject>()) {''')
    elif path == "js/src/jit/CodeGenerator.cpp":
        text = include_monitor(text, '#include "vm/AsyncFunction.h"\n')
        anchor = '''  // ********************
  // * Native functions *
  // ********************
  masm.bind(&noJitEntry);
'''
        text = replace_once(text, anchor, anchor + '''  // Generic Ion calls also have a direct ABI path; disabling CallIC alone
  // does not route these calls through the observable VM boundary.
  if (CamoufoxMonitorEnabled()) masm.jump(&vmCall);
''')
    elif path == "js/src/builtin/Eval.cpp":
        text = include_monitor(text, '#include "vm/JSContext.h"\n')
        anchor = '''bool js::IndirectEval(JSContext* cx, unsigned argc, Value* vp) {
  CallArgs args = CallArgsFromVp(argc, vp);
'''
        text = replace_once(text, anchor, anchor + '  CamoufoxMonitorDynamicCall(cx, args, false);\n')
        anchor = '''bool js::DirectEval(JSContext* cx, HandleValue v, MutableHandleValue vp) {
'''
        text = replace_once(text, anchor, anchor + '  CamoufoxMonitorDirectEval(cx, v);\n')
    elif path == "js/src/vm/JSFunction.cpp":
        text = include_monitor(text, '#include "vm/JSContext.h"\n')
        anchor = '''                                  FunctionAsyncKind asyncKind) {
  using namespace frontend;
'''
        text = replace_once(text, anchor, anchor + '  CamoufoxMonitorDynamicCall(cx, args, true);\n')
    elif path == "js/src/proxy/Proxy.cpp":
        text = include_monitor(text, '#include "vm/JSContext.h"\n')
        for operation in ("call", "construct"):
            anchor = f'  return handler->{operation}(cx, proxy, args);\n'
            text = replace_once(text, anchor, '  CamoufoxMonitorNative(cx, args);\n' + anchor)
    elif path == "xpcom/base/CycleCollectedJSRuntime.cpp":
        anchor = '#include "js/Debug.h"\n'
        text = replace_once(text, anchor, '''#include "js/CallAndConstruct.h"
#include "js/CharacterEncoding.h"
#include "js/GlobalObject.h"
#include "js/String.h"
''' + anchor)
        anchor = '#include "nsJSUtils.h"\n'
        text = replace_once(text, anchor, '''#include "mozilla/dom/Document.h"
#include "nsIGlobalObject.h"
#include "nsIPrincipal.h"
#include "nsIURI.h"
#include "nsPIDOMWindow.h"
''' + anchor)
        anchor = '''CycleCollectedJSRuntime::CycleCollectedJSRuntime(JSContext* aCx)
'''
        callback = '''static bool CamoufoxMonitorContext(JSContext* aCx,
                                    JS::MutableHandle<JSString*> aHref,
                                    JS::MutableHandle<JSString*> aToken) {
  JSObject* global = JS::CurrentGlobalOrNull(aCx);
  if (!global) return false;
  nsIGlobalObject* nativeGlobal = xpc::NativeGlobal(global);
  if (!nativeGlobal) return false;
  nsIPrincipal* principal = nativeGlobal->PrincipalOrNull();
  if (!principal) return false;
  nsAutoCString href, origin;
  if (auto* window = nativeGlobal->GetAsInnerWindow()) {
    // ClientInfo stores the original URI and does not follow history/hash.
    auto* document = window->GetExtantDoc();
    if (!document || !document->GetDocumentURI() ||
        NS_FAILED(document->GetDocumentURI()->GetSpec(href))) return false;
  } else {
    auto info = nativeGlobal->GetClientInfo();
    if (!info) return false;
    href = info->URL();
  }
  if (NS_FAILED(principal->GetOriginNoSuffix(origin))) return false;
  aHref.set(JS_NewStringCopyUTF8N(aCx, JS::UTF8Chars(href.get(), href.Length())));
  aToken.set(JS_NewStringCopyUTF8N(aCx, JS::UTF8Chars(origin.get(), origin.Length())));
  return aHref && aToken;
}

'''
        text = replace_once(text, anchor, callback + anchor)
        anchor = '  SetDOMCallbacks(aCx, &DOMcallbacks);\n'
        text = replace_once(text, anchor, anchor + '  JS::SetCamoufoxMonitorContextCallback(CamoufoxMonitorContext);\n')
    else:
        raise ValueError(f"Unsupported native engine source: {path}")
    return text.encode()
