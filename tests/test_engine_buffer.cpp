#include "CamoufoxMonitorBuffer.h"

#include <cassert>
#include <iostream>
#include <limits>
#include <string>

using js::camoufox::FrameVv8Record;
using js::camoufox::Vv8Record;

int main() {
  Vv8Record raw;
  raw.Raw("g17:{7,Window}:");
  raw.Text(u"colon: slash\\ quote\" nul\0 中🦊", true);
  assert(raw.View() == "g17:{7,Window}:\"colon\\: slash\\\\ quote\" nul\"");
  // An explicit-length view keeps embedded NUL and surrogate pairs.
  Vv8Record units;
  constexpr char16_t source[] = {u':', u'\\', u'"', 0, u'\n', u'中',
                                  0xd83e, 0xdd8a};
  units.Text({source, sizeof(source) / sizeof(source[0])}, false);
  assert(units.View() == "\\:\\\\\"\\x00\\x0a\\u4e2d\\ud83e\\udd8a");
  std::cout << units.View() << '\n';

  Vv8Record numbers;
  numbers.Number(-0.0); numbers.Raw(":");
  numbers.Number(0.123456789); numbers.Raw(":");
  numbers.Signed(-1); numbers.Raw(":");
  numbers.Unsigned(std::numeric_limits<uint64_t>::max());
  assert(numbers.View() == "-0:0.123457:-1:18446744073709551615");
  std::cout << numbers.View() << '\n';

  std::string payload = "$12:\"http\\://127.0.0.1/script.js\":";
  payload += std::string(1025, '%');
  payload += std::string(1025, '\\');
  payload += std::string(1025, '"');
  unsigned frames = 0;
  assert(FrameVv8Record(payload, 11, 22, 33, [&](std::string_view frame) {
    assert(frame.size() <= 4096);
    assert(frame.find('%') == std::string_view::npos);
    ++frames;
    std::cout << frame;
    return true;
  }));
  assert(frames == 7);
  unsigned writes = 0;
  assert(!FrameVv8Record(payload, 11, 22, 34, [&](std::string_view) {
    return ++writes < 2;
  }));
  assert(writes == 2);
  assert(!FrameVv8Record("g\n", 1, 1, 1, [](auto) { return true; }));
  assert(!FrameVv8Record("", 1, 1, 1, [](auto) { return true; }));
  assert(!FrameVv8Record("!1", 0, 1, 1, [](auto) { return true; }));
}
