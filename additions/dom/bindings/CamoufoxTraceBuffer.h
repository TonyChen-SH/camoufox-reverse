/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this file,
 * You can obtain one at https://mozilla.org/MPL/2.0/. */

#ifndef mozilla_dom_CamoufoxTraceBuffer_h
#define mozilla_dom_CamoufoxTraceBuffer_h

#include <charconv>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <string_view>

namespace mozilla::camoutrace {

template <size_t Capacity>
class JsonBuffer final {
 public:
  void Raw(std::string_view text) {
    if (text.size() > Capacity - mSize) {
      mFailed = true;
      return;
    }
    if (!text.empty()) std::memcpy(mData + mSize, text.data(), text.size());
    mSize += text.size();
  }

  void UInt(uint64_t value) {
    char number[32];
    auto result = std::to_chars(number, number + sizeof(number), value);
    Raw(std::string_view(number, result.ptr - number));
  }

  void Number(double value) {
    if (!std::isfinite(value)) {
      String(std::isnan(value) ? "NaN" : value < 0 ? "-Infinity" : "Infinity");
      return;
    }
    char number[64];
    auto result = std::to_chars(number, number + sizeof(number), value);
    if (result.ec != std::errc()) {
      mFailed = true;
      return;
    }
    Raw(std::string_view(number, result.ptr - number));
  }

  bool String(std::string_view text, size_t limit = 256) {
    Raw("\"");
    size_t used = 0;
    size_t offset = 0;
    bool changed = false;
    const char hex[] = "0123456789abcdef";
    while (offset < text.size()) {
      const auto first = static_cast<unsigned char>(text[offset]);
      char escaped[6];
      size_t consumed = 1;
      std::string_view piece(text.data() + offset, 1);
      if (first == '"' || first == '\\') {
        escaped[0] = '\\';
        escaped[1] = static_cast<char>(first);
        piece = {escaped, 2};
      } else if (first < 0x20) {
        escaped[0] = '\\'; escaped[1] = 'u';
        escaped[2] = '0'; escaped[3] = '0';
        escaped[4] = hex[first >> 4]; escaped[5] = hex[first & 15];
        piece = {escaped, 6};
      } else if (first >= 0x80) {
        size_t length = first >= 0xC2 && first <= 0xDF ? 2 :
                        first >= 0xE0 && first <= 0xEF ? 3 :
                        first >= 0xF0 && first <= 0xF4 ? 4 : 0;
        bool valid = length && offset + length <= text.size();
        for (size_t i = 1; valid && i < length; ++i) {
          auto byte = static_cast<unsigned char>(text[offset + i]);
          valid = byte >= 0x80 && byte <= 0xBF;
        }
        if (valid && length >= 3) {
          auto second = static_cast<unsigned char>(text[offset + 1]);
          valid = !(first == 0xE0 && second < 0xA0) &&
                  !(first == 0xED && second >= 0xA0) &&
                  !(first == 0xF0 && second < 0x90) &&
                  !(first == 0xF4 && second >= 0x90);
        }
        if (valid) {
          consumed = length;
          piece = {text.data() + offset, length};
        } else {
          piece = "\\ufffd";
          changed = true;
        }
      }
      if (piece.size() > limit - used) {
        changed = true;
        break;
      }
      Raw(piece);
      used += piece.size();
      offset += consumed;
    }
    Raw("\"");
    mTruncated |= changed;
    return changed;
  }

  void MarkTruncated() { mTruncated = true; }
  bool Failed() const { return mFailed; }
  bool Truncated() const { return mTruncated; }
  std::string_view View() const { return {mData, mSize}; }

 private:
  char mData[Capacity];
  size_t mSize = 0;
  bool mFailed = false;
  bool mTruncated = false;
};

}  // namespace mozilla::camoutrace
#endif
