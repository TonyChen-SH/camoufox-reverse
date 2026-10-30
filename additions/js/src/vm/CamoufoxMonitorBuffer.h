/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/. */

#ifndef vm_CamoufoxMonitorBuffer_h
#define vm_CamoufoxMonitorBuffer_h

#include <charconv>
#include <cstdint>
#include <locale>
#include <sstream>
#include <string>
#include <string_view>

#include "../../public/experimental/CamoufoxTraceBuffer.h"

namespace js::camoufox {

// The reference format escapes UTF-16 units and deliberately does not quote
// embedded double quotes. Keep this grammar separate from its JSON transport.
class Vv8Record final {
 public:
  void Raw(std::string_view text) { mText.append(text); }
  void Unit(char16_t unit) {
    static constexpr char hex[] = "0123456789abcdef";
    if (unit < 0x20) {
      char escaped[] = {'\\', 'x', hex[unit >> 4], hex[unit & 15]};
      mText.append(escaped, sizeof(escaped));
    } else if (unit <= 0x7e) {
      if (unit == ':' || unit == '\\') mText.push_back('\\');
      mText.push_back(static_cast<char>(unit));
    } else {
      char escaped[] = {'\\', 'u', hex[unit >> 12], hex[(unit >> 8) & 15],
                        hex[(unit >> 4) & 15], hex[unit & 15]};
      mText.append(escaped, sizeof(escaped));
    }
  }
  void Text(std::u16string_view text, bool quotes = true) {
    if (quotes) Raw("\"");
    for (char16_t unit : text) Unit(unit);
    if (quotes) Raw("\"");
  }
  void Ascii(std::string_view text, bool quotes = false) {
    if (quotes) Raw("\"");
    for (unsigned char byte : text) Unit(byte);
    if (quotes) Raw("\"");
  }
  void Unsigned(uint64_t value, int base = 10) {
    char buffer[32];
    auto end = std::to_chars(buffer, buffer + sizeof(buffer), value, base);
    Raw({buffer, static_cast<size_t>(end.ptr - buffer)});
  }
  void Signed(int64_t value) {
    char buffer[32];
    auto end = std::to_chars(buffer, buffer + sizeof(buffer), value);
    Raw({buffer, static_cast<size_t>(end.ptr - buffer)});
  }
  void Number(double value) {
    std::ostringstream stream;
    stream.imbue(std::locale::classic());
    stream << value;
    Raw(stream.str());
  }
  std::string_view View() const { return mText; }

 private:
  std::string mText;
};

template <typename Sink>
bool FrameVv8Record(std::string_view raw, uint64_t pid, uint64_t tid,
                    uint64_t sequence, Sink&& sink) {
  if (raw.empty() || !pid || !tid || !sequence) return false;
  for (unsigned char byte : raw) {
    if (byte < 0x20 || byte > 0x7e) return false;
  }
  // '%' expands to six JSON bytes to survive the browser driver's printf
  // sink. Even the worst possible fragment plus framing stays below PIPE_BUF.
  constexpr size_t fragmentSize = 512;
  uint64_t part = 0;
  for (size_t start = 0; start < raw.size(); start += fragmentSize, ++part) {
    auto fragment = raw.substr(start, fragmentSize);
    mozilla::camoutrace::JsonBuffer<4096> envelope;
    envelope.Raw("CAMOUFOX_TRACE {\"schema\":1,\"event\":\"vv8\",\"pid\":");
    envelope.UInt(pid);
    envelope.Raw(",\"tid\":"); envelope.UInt(tid);
    envelope.Raw(",\"record\":"); envelope.UInt(sequence);
    envelope.Raw(",\"part\":"); envelope.UInt(part);
    envelope.Raw(start + fragment.size() == raw.size()
                     ? ",\"final\":true,\"data\":"
                     : ",\"final\":false,\"data\":");
    envelope.String(fragment, fragmentSize * 6);
    envelope.Raw("}\n");
    if (envelope.Failed() || envelope.Truncated() || !sink(envelope.View())) {
      return false;
    }
  }
  return true;
}

}  // namespace js::camoufox
#endif
