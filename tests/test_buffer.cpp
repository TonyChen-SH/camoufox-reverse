#include "CamoufoxTraceBuffer.h"

#include <cassert>
#include <iostream>
#include <limits>
#include <string>

using mozilla::camoutrace::JsonBuffer;

int main() {
  JsonBuffer<256> controls;
  controls.String(std::string("quote\" slash\\ nul\0 newline\n", 27));
  assert(!controls.Failed());
  std::cout << controls.View() << '\n';

  JsonBuffer<64> unicode;
  unicode.String("\xe4\xb8\xad\xf0\x9f\xa6\x8a", 6);
  assert(unicode.Truncated());
  assert(unicode.View() == "\"\xe4\xb8\xad\"");
  std::cout << unicode.View() << '\n';

  JsonBuffer<64> invalid;
  invalid.String(std::string("\xc0\xaf\xed\xa0\x80", 5));
  assert(invalid.Truncated());
  std::cout << invalid.View() << '\n';

  JsonBuffer<128> numbers;
  numbers.Raw("[");
  numbers.UInt(std::numeric_limits<uint64_t>::max());
  numbers.Raw(","); numbers.Number(0.125);
  numbers.Raw(","); numbers.Number(std::numeric_limits<double>::infinity());
  numbers.Raw(","); numbers.Number(-std::numeric_limits<double>::infinity());
  numbers.Raw(","); numbers.Number(std::numeric_limits<double>::quiet_NaN());
  numbers.Raw("]");
  assert(!numbers.Failed());
  std::cout << numbers.View() << '\n';

  JsonBuffer<16> escaped;
  escaped.String("\nrest", 5);
  assert(escaped.View() == "\"\"");
  assert(escaped.Truncated());

  JsonBuffer<4> overflow;
  overflow.Raw("1234");
  overflow.Raw("5");
  assert(overflow.Failed());
  assert(overflow.View() == "1234");

  JsonBuffer<8> empty;
  empty.Raw(std::string_view{});
  empty.String("");
  assert(empty.View() == "\"\"");
}
