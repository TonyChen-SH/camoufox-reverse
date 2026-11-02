var global = (function() { return this; })();

function hotProbe(start, end, key) {
  for (var i = start; i < end; ++i) {
    Reflect.set(global, key, i);
    print(Reflect.get(global, key));
  }
}

%PrepareFunctionForOptimization(hotProbe);
hotProbe(-2, -1, 'warm');
hotProbe(-2, -1, 'warm');
%OptimizeFunctionOnNextCall(hotProbe);
hotProbe(0, 1339, 'bar');
// Pinned runtime.h: kOptimized=1<<3, kTurboFanned=1<<5.
if ((%GetOptimizationStatus(hotProbe) & 40) !== 40) {
  throw new Error('hotProbe did not remain optimized');
}
