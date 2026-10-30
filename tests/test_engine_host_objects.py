"""Execute the real class serializer, namespace creation and host predicate."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
from engine_native_patch import transform
from test_engine_emitter import cpp_function


MODEL = r'''
#include <cassert>
#include <iostream>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <type_traits>
#include <unordered_set>
#include <vector>
#include "CamoufoxMonitorBuffer.h"
using js::camoufox::Vv8Record;
namespace mozilla { template<typename T> using Maybe = std::optional<T>; }
struct JSObject;
struct JSFunction;
namespace js {
struct GlobalObject;
struct NativeObject;
struct PlainObject;
struct ArrayObject;
struct ProxyObject;
struct WrapperObject;
}
struct JSClass {
  const char* name;
  bool dom = false;
  bool isDOMClass() const { return dom; }
  void* getResolve() const { return nullptr; }
};
struct JSRuntime {
  const JSClass* windowProxyClass = nullptr;
  const JSClass* maybeWindowProxyClass() const { return windowProxyClass; }
};
JSRuntime runtime;
struct JSString { std::string text; size_t length() const { return text.length(); } };
using JSAtom = JSString;
struct jsid {
  int value = 0;
  jsid(int number = 0): value(number) {}
  bool isSymbol() const { return false; }
  bool isInt() const { return true; }
  int toInt() const { return value; }
  JSAtom* toAtom() const { return nullptr; }
};
struct JSContext {
  struct Names { int constructor = 1; } names_;
  const Names& names() const { return names_; }
};
namespace JS {
template<typename T> using Handle = T;
template<typename T> using Heap = T;
template<typename T> struct Rooted {
  T data;
  Rooted(JSContext*, T value = T{}): data(value) {}
  operator T() const { return data; }
  auto operator->() const {
    if constexpr (std::is_pointer_v<T>) return data;
    else return data.operator->();
  }
  bool isSome() const { return data.has_value(); }
  bool isSymbol() const { return data.isSymbol(); }
  bool isInt() const { return data.isInt(); }
  int toInt() const { return data.toInt(); }
  JSAtom* toAtom() const { return data.toAtom(); }
  Rooted& operator=(T value) { data = value; return *this; }
  void set(T value) { data = value; }
};
using RootedObject = Rooted<JSObject*>;
using RootedFunction = Rooted<JSFunction*>;
using RootedString = Rooted<JSString*>;
using RootedId = Rooted<jsid>;
using HandleObject = JSObject*;
struct Value {
  JSObject* object = nullptr;
  bool isObject() const { return object != nullptr; }
  JSObject& toObject() const { return *object; }
};
using HandleValue = const Value&;
using RootedValue = Rooted<Value>;
Value UndefinedValue() { return {}; }
struct RootedIdVector : std::vector<jsid> {
  explicit RootedIdVector(JSContext*) {}
  size_t length() const { return size(); }
};
struct PropertyDescriptor {
  bool isDataDescriptor() const { return false; }
  Value value() const { return {}; }
};
struct PropertyKey { static jsid NonIntAtom(JSString*) { return 1; } };
void RegisterCamoufoxHostClass(const JSClass*);
}
enum class Kind { Native, Global, Plain, Array, Function, Proxy };
struct JSObject {
  const JSClass* clasp;
  Kind kind = Kind::Native;
  uint64_t identity = 0;
  // The live Realm global is separate from the proxy's private target.
  js::GlobalObject* global = nullptr;
  JSObject* prototype = nullptr;
  explicit JSObject(const JSClass* type, Kind objectKind = Kind::Native): clasp(type), kind(objectKind) {}
  const JSClass* getClass() const { return clasp; }
  JSRuntime* runtimeFromAnyThread() const { return &runtime; }
  js::GlobalObject& nonCCWGlobal() const { assert(global); return *global; }
  JSObject* staticPrototype() const { return prototype; }
  template<typename T> bool is() const {
    if constexpr (std::is_same_v<T, js::GlobalObject>) return kind == Kind::Global;
    if constexpr (std::is_same_v<T, js::PlainObject>) return kind == Kind::Plain;
    if constexpr (std::is_same_v<T, js::ArrayObject>) return kind == Kind::Array;
    if constexpr (std::is_same_v<T, js::NativeObject>) return kind != Kind::Proxy;
    if constexpr (std::is_same_v<T, js::ProxyObject>) return kind == Kind::Proxy;
    if constexpr (std::is_same_v<T, JSFunction>) return kind == Kind::Function;
    return false;
  }
  template<typename T> T& as() { return static_cast<T&>(*this); }
};
namespace js {
struct GlobalObject : JSObject {
  explicit GlobalObject(const JSClass* clasp): JSObject(clasp, Kind::Global) {}
};
struct NativeObject : JSObject { using JSObject::JSObject; };
struct PlainObject : JSObject { using JSObject::JSObject; };
struct ArrayObject : JSObject { using JSObject::JSObject; };
struct BaseProxyHandler {
  const void* family_;
  constexpr explicit BaseProxyHandler(const void* family, bool = false): family_(family) {}
  const void* family() const { return family_; }
  // The actual public signature is filled from the pinned Proxy.h below.
  CLASS_NAME_DECLARATION
};
using ProxyHandler = BaseProxyHandler;
struct ProxyObject : JSObject {
  ProxyHandler handler_;
  const ProxyHandler* nativeHandler = nullptr;
  JSObject* privateTarget = nullptr;
  ProxyObject(const JSClass* clasp, const void* family): JSObject(clasp, Kind::Proxy), handler_{family} {}
  const ProxyHandler* handler() const { return nativeHandler ? nativeHandler : &handler_; }
};
struct WrapperObject : ProxyObject {
  using ProxyObject::ProxyObject;
  JSObject* target() const { return privateTarget; }
};
struct Wrapper { static const char family; };
bool IsProxy(const JSObject* object) { return object->kind == Kind::Proxy; }
const ProxyHandler* GetProxyHandler(const JSObject* object) {
  return static_cast<const ProxyObject*>(object)->handler();
}
JSObject* MaybeForwarded(JSObject* object) { return object; }
JSObject* UncheckedUnwrapWithoutExpose(JSObject*);
bool IsWrapper(const JSObject*);
extern const JSClass ProxyClass;
int domFamily, userFamily, deadFamily;
const void* GetDOMProxyHandlerFamily() { return &domFamily; }
const void* GetDOMRemoteProxyHandlerFamily();
bool IsWindowProxy(JSObject*);
JSObject* ToWindowIfWindowProxy(JSObject*);
int NameToId(int value) { return value; }
namespace gc {
bool GetOrCreateUniqueId(JSObject* object, uint64_t* identity) { *identity = object->identity; return true; }
}
bool GetPropertyKeys(JSContext*, JSObject*, unsigned, JS::RootedIdVector*) { return false; }
unsigned descriptorLookups = 0;
bool GetOwnPropertyDescriptor(JSContext*, JSObject*, jsid,
                             JS::Rooted<mozilla::Maybe<JS::PropertyDescriptor>>* descriptor) {
  descriptorLookups++;
  descriptor->set(std::nullopt);
  return true;
}
}
struct JSFunction : JSObject {
  using JSObject::JSObject;
  bool getDisplayAtom(JSContext*, JS::Rooted<JSAtom*>* name) { name->set(nullptr); return true; }
};
bool AppendString(JSContext*, JSString* string, Vv8Record& record, bool quotes) {
  record.Ascii(string->text, quotes); return true;
}
bool AppendValue(JSContext*, JS::HandleValue, Vv8Record&, bool = false) { return false; }
constexpr unsigned JSITER_OWNONLY = 1;
struct Config { bool enabled = true; } config;
const Config& Configuration() { return config; }
std::mutex gNativeMutex;
std::unordered_set<const JSClass*> gHostClasses;
struct NativeProperties {};
struct DOMIfaceAndProtoJSClass {
  const JSClass* clasp;
  const JSClass* ToJSClass() const { return clasp; }
};
std::vector<std::unique_ptr<JSObject>> objects;
std::vector<std::unique_ptr<JSString>> strings;
JSString* JS_AtomizeString(JSContext*, const char* name) {
  auto string = std::make_unique<JSString>();
  string->text = name;
  strings.push_back(std::move(string)); return strings.back().get();
}
JSObject* JS_NewObjectWithGivenProto(JSContext*, const JSClass* clasp, JSObject* prototype) {
  auto object = std::make_unique<JSObject>(clasp);
  object->prototype = prototype;
  objects.push_back(std::move(object)); return objects.back().get();
}
namespace nsContentUtils { bool ThreadsafeIsSystemCaller(JSContext*) { return false; } }
bool InitInterfaceOrNamespaceObject(JSContext*, JSObject*, const NativeProperties*,
                                    const NativeProperties*, bool) { return true; }
bool DefineConstructor(JSContext*, JSObject*, jsid, JSObject*) { return true; }
bool DefineToStringTag(JSContext*, JSObject*, JSString*) { return true; }
#define PROXY_CLASS_DEF(name, flags) JSClass{name, false}
#define PROXY_CLASS_DEF_WITH_CLASS_SPEC(name, flags, spec) JSClass{name, false}
#define JSCLASS_HAS_RESERVED_SLOTS(slots) 0
#define MOZ_LIKELY(value) (value)
#define MOZ_ASSERT(value) assert(value)
'''


class EngineHostObjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ["FIREFOX_SOURCE"])
        cls.producer = (ROOT / "additions/js/src/vm/CamoufoxMonitor.cpp").read_text()
        outer = (cls.source / "dom/base/nsGlobalWindowOuter.cpp").read_text()
        start = outer.index("const JSClass OuterWindowProxyClass =")
        cls.outer_class = outer[start:outer.index(";", start) + 1]
        window = (cls.source / "js/src/vm/WindowProxy.cpp").read_text()
        cls.window_functions = (cpp_function(window, "bool js::IsWindowProxy(") + "\n" +
                                cpp_function(window, "JSObject* js::ToWindowIfWindowProxy("))
        proxy_header = (cls.source / "js/public/Proxy.h").read_text()
        start = proxy_header.index("  virtual const char* className(")
        signature = proxy_header[start:proxy_header.index(";", start)]
        cls.model = MODEL.replace("CLASS_NAME_DECLARATION", signature +
                                 ' { (void)cx; (void)proxy; assert(false && "unapproved className dispatch"); return nullptr; }')
        remote_header = (cls.source / "dom/bindings/RemoteObjectProxy.h").read_text()
        remote_cpp = (cls.source / "dom/bindings/RemoteObjectProxy.cpp").read_text()
        remote_outer = (cls.source / "dom/base/RemoteOuterWindowProxy.cpp").read_text()
        start = remote_header.index("  const char* className(")
        declaration = remote_header[start:remote_header.index(";", start) + 1]
        start = remote_outer.index("template <>\nconst JSClass RemoteOuterWindowProxy::Base::sClass =")
        remote_class = remote_outer[start:remote_outer.index(";", start) + 1]
        remote_class_name = cpp_function(remote_cpp, "const char* RemoteObjectProxyBase::className(")
        # Its context parameter is unused upstream; omitting the name preserves
        # the actual API/body under this model's stricter unused-parameter check.
        remote_class_name = remote_class_name.replace("JSContext* aCx,", "JSContext*,", 1)
        cls.remote_functions = r'''
namespace mozilla::dom {
namespace prototypes {
enum ID { Window, Location };
namespace id { constexpr ID Window = prototypes::Window; }
}
unsigned remoteClassNameCalls = 0;
const char* NamesOfInterfacesWithProtos(prototypes::ID id) {
  remoteClassNameCalls++;
  return id == prototypes::Window ? "Window" : "Location";
}
struct RemoteObjectProxyBase : js::BaseProxyHandler {
  static const char sCrossOriginProxyFamily;
  prototypes::ID mPrototypeID;
''' + cpp_function(remote_header, "  explicit constexpr RemoteObjectProxyBase(") + "\n" + declaration + r'''
};
const char RemoteObjectProxyBase::sCrossOriginProxyFamily = 0;
template<typename Native> struct RemoteProxy : RemoteObjectProxyBase {
  using RemoteObjectProxyBase::RemoteObjectProxyBase;
  static const JSClass sClass;
};
struct RemoteOuterWindowProxy : RemoteProxy<void> {
  using RemoteObjectProxy = RemoteProxy<void>;
  using Base = RemoteObjectProxy;
''' + cpp_function(remote_outer, "  constexpr RemoteOuterWindowProxy()") + "\n};\n" + remote_class + "\n" + \
            remote_class_name + r'''
}
const void* js::GetDOMRemoteProxyHandlerFamily() {
  return &mozilla::dom::RemoteObjectProxyBase::sCrossOriginProxyFamily;
}
'''
        wrappers = (cls.source / "js/src/proxy/Wrapper.cpp").read_text()
        wrapper_header = (cls.source / "js/public/Wrapper.h").read_text()
        wrapper_object = (cls.source / "js/src/vm/WrapperObject.h").read_text()
        proxy = (cls.source / "js/src/proxy/Proxy.cpp").read_text()
        start = proxy.index("const JSClass js::ProxyClass =")
        cls.wrapper_functions = (
            "namespace js { const char Wrapper::family = 0;\n" +
            cpp_function(wrapper_header, "inline bool IsWrapper(") + "\n}\n" +
            "template<>\n" + cpp_function(wrapper_object, "inline bool JSObject::is<js::WrapperObject>(") + "\n" +
            cpp_function(wrappers, "JSObject* js::UncheckedUnwrapWithoutExpose(") + "\n" +
            proxy[start:proxy.index(";", start) + 1])
        cls.host_functions = (cpp_function(cls.producer, "void JS::RegisterCamoufoxHostClass(") + "\n" +
                              cpp_function(cls.producer, "bool HostObject("))
        for name in ("JSObject* HostTarget(", "bool DirectHostObject("):
            if name in cls.producer:
                cls.host_functions = cpp_function(cls.producer, name) + "\n" + cls.host_functions

    def execute(self, functions, main):
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / "host.cpp"
            binary = Path(directory) / "host"
            cpp.write_text(self.model + self.outer_class + "\n" + self.remote_functions + "\n" +
                           self.window_functions + "\n" + self.wrapper_functions + "\n" +
                           self.host_functions + "\n" + functions + "\n" + main)
            compiled = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++20", "-Wall", "-Wextra",
                                       "-Werror", "-fsanitize=undefined", "-fno-sanitize-recover=all",
                                       "-I", str(ROOT / "additions/js/src/vm"), str(cpp), "-o", str(binary)],
                                      text=True, capture_output=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.splitlines()

    def test_window_proxy_uses_native_window_class_without_relabeling_other_proxies(self):
        function = cpp_function(self.producer, "bool AppendClass(")
        output = self.execute(function, r'''
int main() {
  JSContext cx;
  runtime.windowProxyClass = &OuterWindowProxyClass;
  JSClass windowClass{"Window", true}, chromeClass{"ChromeWindow", true};
  JSClass ordinaryProxyClass{"Proxy"}, collectionClass{"HTMLCollection"};
  js::GlobalObject inner(&windowClass), chromeInner(&chromeClass);
  js::ProxyObject outer(&OuterWindowProxyClass, &js::userFamily);
  js::ProxyObject chromeOuter(&OuterWindowProxyClass, &js::userFamily);
  js::ProxyObject userProxy(&ordinaryProxyClass, &js::userFamily);
  js::ProxyObject collection(&collectionClass, &js::domFamily);
  outer.global = &inner; chromeOuter.global = &chromeInner;
  userProxy.global = &inner;
  for (JSObject* object : std::vector<JSObject*>{&outer, &userProxy, &collection, &chromeOuter}) {
    Vv8Record record;
    bool plain = true;
    assert(AppendClass(&cx, object, record, &plain) && !plain);
    std::cout << record.View() << '\n';
  }
  std::cout << HostObject(JS::Value{&outer}) << ':' << HostObject(JS::Value{&userProxy})
            << ':' << HostObject(JS::Value{&collection}) << '\n';
  assert(js::descriptorLookups == 0);
}
''')
        self.assertEqual(output, ["Window", "Proxy", "HTMLCollection", "ChromeWindow", "1:0:1"])

    def test_real_namespace_creation_registers_exact_class_for_the_host_predicate(self):
        path = "dom/bindings/BindingUtils.cpp"
        source = transform(path, (self.source / path).read_bytes()).decode()
        function = cpp_function(source, "void CreateNamespaceObject(")
        output = self.execute(function, r'''
int main() {
  JSContext cx;
  JSClass globalClass{"Window", true}, protoClass{"Object"};
  JSClass consoleClass{"console"}, counterfeitClass{"console"}, disabledClass{"disabledNamespace"};
  js::GlobalObject global(&globalClass);
  JSObject prototype(&protoClass), counterfeit(&counterfeitClass);
  DOMIfaceAndProtoJSClass consoleNamespace{&consoleClass}, disabledNamespace{&disabledClass};
  JSObject* cache = nullptr;
  CreateNamespaceObject(&cx, &global, &prototype, consoleNamespace, &cache,
                        nullptr, nullptr, "console", true);
  assert(cache && cache->getClass() == &consoleClass && cache->prototype == &prototype);
  std::cout << HostObject(JS::Value{cache}) << ':' << HostObject(JS::Value{&counterfeit}) << '\n';
  config.enabled = false;
  cache = nullptr;
  CreateNamespaceObject(&cx, &global, &prototype, disabledNamespace, &cache,
                        nullptr, nullptr, "disabledNamespace", false);
  assert(cache && cache->getClass() == &disabledClass);
  std::cout << HostObject(JS::Value{cache}) << ':' << gHostClasses.size() << '\n';
}
''')
        self.assertEqual(output, ["1:0", "0:1"])

    def test_targetless_and_dead_window_proxies_use_the_live_realm_global(self):
        function = cpp_function(self.producer, "bool AppendClass(")
        output = self.execute(function, r'''
int main() {
  JSContext cx;
  runtime.windowProxyClass = &OuterWindowProxyClass;
  JSClass windowClass{"Window", true}, ordinaryProxyClass{"Proxy"};
  js::GlobalObject inner(&windowClass);
  js::ProxyObject targetless(&OuterWindowProxyClass, &js::userFamily);
  js::ProxyObject deadWindow(&OuterWindowProxyClass, &js::deadFamily);
  js::ProxyObject deadOrdinary(&ordinaryProxyClass, &js::deadFamily);
  for (js::ProxyObject* object : {&targetless, &deadWindow, &deadOrdinary}) {
    object->global = &inner;
    assert(!object->privateTarget);
    Vv8Record record;
    bool plain = true;
    assert(AppendClass(&cx, object, record, &plain) && !plain);
    std::cout << record.View() << '\n';
  }
  assert(js::ToWindowIfWindowProxy(&targetless) == &inner);
  assert(js::ToWindowIfWindowProxy(&deadWindow) == &inner);
  assert(js::ToWindowIfWindowProxy(&deadOrdinary) == &deadOrdinary);
  assert(js::descriptorLookups == 0);
}
''')
        self.assertEqual(output, ["Window", "Window", "Proxy"])

    def test_real_native_wrappers_preserve_host_class_and_reject_scripted_proxy_targets(self):
        function = cpp_function(self.producer, "bool AppendClass(")
        output = self.execute(function, r'''
int main() {
  JSContext cx;
  runtime.windowProxyClass = &OuterWindowProxyClass;
  JSClass windowClass{"Window", true}, navigatorClass{"Navigator", true}, elementClass{"HTMLDivElement", true};
  JSClass plainClass{"Object"}, consoleClass{"console"};
  js::GlobalObject inner(&windowClass);
  JSObject navigator(&navigatorClass), element(&elementClass), console(&consoleClass);
  js::PlainObject plain(&plainClass, Kind::Plain);
  js::ProxyObject scripted(&js::ProxyClass, &js::userFamily);
  scripted.privateTarget = &navigator;
  js::WrapperObject outer(&OuterWindowProxyClass, &js::Wrapper::family);
  outer.global = &inner; outer.privateTarget = &inner;
  JS::RegisterCamoufoxHostClass(&consoleClass);
  for (JSObject* target : std::vector<JSObject*>{&outer, &navigator, &element, &console, &plain, &scripted, nullptr}) {
    js::WrapperObject wrapper(&js::ProxyClass, &js::Wrapper::family);
    wrapper.privateTarget = target;
    Vv8Record record;
    bool plainConstructor = true;
    assert(AppendClass(&cx, &wrapper, record, &plainConstructor));
    assert(!plainConstructor && wrapper.privateTarget == target);
    std::cout << HostObject(JS::Value{&wrapper}) << ':' << record.View() << '\n';
  }
  assert(!HostObject(JS::Value{&scripted}));
  assert(js::descriptorLookups == 0);
}
''')
        self.assertEqual(output, ["1:Window", "1:Navigator", "1:HTMLDivElement", "1:console",
                                  "0:Proxy", "0:Proxy", "0:Proxy"])

    def test_wrapped_host_summary_keeps_original_wrapper_identity(self):
        functions = (cpp_function(self.producer, "bool AppendClass(") + "\n" +
                     cpp_function(self.producer, "bool AppendObject("))
        output = self.execute(functions, r'''
int main() {
  JSContext cx;
  JSClass navigatorClass{"Navigator", true};
  JSObject navigator(&navigatorClass);
  navigator.identity = 19;
  js::WrapperObject wrapper(&js::ProxyClass, &js::Wrapper::family);
  wrapper.identity = 77;
  wrapper.privateTarget = &navigator;
  Vv8Record record;
  assert(AppendObject(&cx, &wrapper, record, true));
  std::cout << record.View() << '\n';
  assert(wrapper.privateTarget == &navigator && wrapper.identity == 77);
  assert(js::descriptorLookups == 0);
}
''')
        self.assertEqual(output, ["{77,Navigator}"])

    def test_real_remote_window_provider_names_keep_wrapper_identity_without_traps(self):
        functions = (cpp_function(self.producer, "bool AppendClass(") + "\n" +
                     cpp_function(self.producer, "bool AppendObject("))
        output = self.execute(functions, r'''
int main() {
  JSContext cx;
  runtime.windowProxyClass = &OuterWindowProxyClass;
  using namespace mozilla::dom;
  RemoteOuterWindowProxy windowHandler;
  RemoteObjectProxyBase otherHandler(prototypes::Location);
  JSClass otherRemoteClass{"OtherRemoteClass"}, scriptedClass{"Proxy"};
  js::ProxyObject remote(&RemoteOuterWindowProxy::Base::sClass, js::GetDOMRemoteProxyHandlerFamily());
  remote.nativeHandler = &windowHandler;
  remote.identity = 19;
  assert(!js::IsWindowProxy(&remote) && !remote.global && !remote.privateTarget);
  js::WrapperObject wrapper(&js::ProxyClass, &js::Wrapper::family);
  wrapper.privateTarget = &remote; wrapper.identity = 77;
  js::ProxyObject otherRemote(&otherRemoteClass, js::GetDOMRemoteProxyHandlerFamily());
  otherRemote.nativeHandler = &otherHandler;
  js::ProxyObject scripted(&scriptedClass, &js::userFamily);
  scripted.privateTarget = &remote;
  for (JSObject* object : std::vector<JSObject*>{&remote, &wrapper, &otherRemote, &scripted}) {
    Vv8Record record;
    bool plain = true;
    assert(AppendClass(&cx, object, record, &plain) && !plain);
    std::cout << HostObject(JS::Value{object}) << ':' << record.View() << '\n';
  }
  Vv8Record summary;
  assert(AppendObject(&cx, &wrapper, summary, true));
  std::cout << summary.View() << '\n';
  assert(wrapper.privateTarget == &remote && remote.identity == 19 && wrapper.identity == 77);
  std::cout << remoteClassNameCalls << '\n';
  assert(js::descriptorLookups == 0);
}
''')
        self.assertEqual(output, ["1:Window", "1:Window", "1:Location", "0:Proxy", "{77,Window}", "4"])


if __name__ == "__main__":
    unittest.main()
