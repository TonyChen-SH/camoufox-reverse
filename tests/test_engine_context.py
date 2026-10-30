"""Execute the injected native context callback against changing provider data."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from engine_native_patch import transform


MODEL = r'''
#include <iostream>
#include <memory>
#include <optional>
#include <string>
#include <vector>
class nsAutoCString {
 public:
  std::string text;
  nsAutoCString() = default;
  explicit nsAutoCString(const char* value): text(value) {}
  const char* get() const { return text.data(); }
  size_t Length() const { return text.size(); }
};
struct URI {
  nsAutoCString spec{"https://site.test/current?state=2#changed"};
  int GetSpec(nsAutoCString& out) { out = spec; return 0; }
};
struct Document { URI uri; URI* GetDocumentURI() { return &uri; } };
struct Window { Document doc; Document* GetExtantDoc() { return &doc; } };
struct Info {
  nsAutoCString url{"https://site.test/original"};
  const nsAutoCString& URL() const { return url; }
};
struct nsIPrincipal {
  int GetOriginNoSuffix(nsAutoCString& out) {
    out = nsAutoCString("https://site.test"); return 0;
  }
};
struct nsIGlobalObject {
  bool isWindow = true;
  Window window;
  nsIPrincipal principal;
  std::optional<Info> info{Info{}};
  std::optional<Info> GetClientInfo() const { return info; }
  nsIPrincipal* PrincipalOrNull() { return &principal; }
  Window* GetAsInnerWindow() { return isWindow ? &window : nullptr; }
};
struct JSObject { nsIGlobalObject* native; };
struct JSContext { JSObject* global; };
struct JSString { std::string text; };
namespace JS {
template<typename T> struct MutableHandle {
  T* address;
  MutableHandle(T* value): address(value) {}
  void set(T value) { *address = value; }
  operator T() const { return *address; }
};
struct UTF8Chars {
  const char* data; size_t length;
  UTF8Chars(const char* text, size_t size): data(text), length(size) {}
};
JSObject* CurrentGlobalOrNull(JSContext* cx) { return cx->global; }
}
namespace xpc {
nsIGlobalObject* NativeGlobal(JSObject* object) { return object->native; }
}
static std::vector<std::unique_ptr<JSString>> strings;
JSString* JS_NewStringCopyUTF8N(JSContext*, JS::UTF8Chars chars) {
  auto text = std::make_unique<JSString>();
  text->text.assign(chars.data, chars.length);
  strings.push_back(std::move(text));
  return strings.back().get();
}
#define NS_FAILED(value) ((value) != 0)
'''

MAIN = r'''
int main() {
  nsIGlobalObject provider;
  JSObject global{&provider};
  JSContext cx{&global};
  JSString *href = nullptr, *token = nullptr;
  if (!CamoufoxMonitorContext(&cx, &href, &token)) return 1;
  std::cout << href->text << '\n' << token->text << '\n';
  provider.window.doc.uri.spec = nsAutoCString("https://site.test/new#after-history");
  if (!CamoufoxMonitorContext(&cx, &href, &token)) return 2;
  std::cout << href->text << '\n';
  provider.isWindow = false;
  provider.info->url = nsAutoCString("https://site.test/worker.js");
  if (!CamoufoxMonitorContext(&cx, &href, &token)) return 3;
  std::cout << href->text << '\n';
  provider.info.reset();
  if (CamoufoxMonitorContext(&cx, &href, &token)) return 4;
  cx.global = nullptr;
  if (CamoufoxMonitorContext(&cx, &href, &token)) return 5;
}
'''


class EngineContextTests(unittest.TestCase):
    def test_real_callback_uses_current_window_uri_and_worker_provider(self):
        source = Path(os.environ["FIREFOX_SOURCE"]) / "xpcom/base/CycleCollectedJSRuntime.cpp"
        text = transform("xpcom/base/CycleCollectedJSRuntime.cpp", source.read_bytes()).decode()
        begin = text.index("static bool CamoufoxMonitorContext(")
        end = text.index("CycleCollectedJSRuntime::CycleCollectedJSRuntime(", begin)
        with tempfile.TemporaryDirectory() as temporary:
            cpp = Path(temporary) / "context.cpp"
            binary = Path(temporary) / "context"
            cpp.write_text(MODEL + text[begin:end] + MAIN)
            subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-Wall",
                            "-Wextra", "-Werror", "-fsanitize=undefined",
                            "-fno-sanitize-recover=all", str(cpp), "-o", str(binary)], check=True)
            result = subprocess.run([str(binary)], text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.splitlines(), [
            "https://site.test/current?state=2#changed", "https://site.test",
            "https://site.test/new#after-history", "https://site.test/worker.js",
        ])


if __name__ == "__main__":
    unittest.main()
