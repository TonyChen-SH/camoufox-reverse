/* Native engine conformance fixture: no external requests or detection claims. */
(async () => {
  const values = {};
  const sources = {};
  const contexts = {main: location.href};
  const storage = localStorage;

  const neverCalled = storage.clear;
  values.methodReference = typeof neverCalled;
  values.missing = window.__vv8_missing_named === undefined;
  const missingKey = "__vv8_missing_keyed";
  values.missingKeyed = window[missingKey] === undefined;
  window.__vv8_named = 3;
  values.namedBefore = window.__vv8_named;
  window.__vv8_named += 2;
  values.namedPre = ++window.__vv8_named;
  values.namedPost = window.__vv8_named++;
  values.namedAfter = window.__vv8_named;

  let keyConversions = 0;
  let receiverEvaluations = 0;
  let rhsEvaluations = 0;
  let rhsConversions = 0;
  const key = {[Symbol.toPrimitive]() { keyConversions++; return "__vv8_key_once"; }};
  const rhs = {valueOf() { rhsConversions++; return 4; }};
  function receiver() { receiverEvaluations++; return window; }
  function rightHandSide() { rhsEvaluations++; return rhs; }
  receiver()[key] = 2;
  receiver()[key] += rightHandSide();
  values.keyedPost = receiver()[key]++;
  values.keyedAfter = window.__vv8_key_once;
  values.coercion = {keyConversions, receiverEvaluations, rhsEvaluations, rhsConversions};

  let failedKeys = 0;
  let failedRHS = 0;
  let assignmentConversions = 0;
  const throwingKey = {[Symbol.toPrimitive]() {
    failedKeys++;
    throw new Error("expected-key-error");
  }};
  const throwingRHS = {valueOf() { failedRHS++; throw new Error("expected-rhs-error"); }};
  try { window[throwingKey] = 1; } catch (error) { values.keyError = error.message; }
  window.__vv8_rhs_throw = 2;
  try { window.__vv8_rhs_throw += throwingRHS; } catch (error) { values.rhsError = error.message; }
  values.afterRhsThrow = window.__vv8_rhs_throw;
  const assigned = {[Symbol.toPrimitive]() {
    assignmentConversions++;
    throw new Error("Assignment must not coerce this object");
  }};
  window.__vv8_object_assigned = assigned;
  values.objectAssignment = window.__vv8_object_assigned === assigned;
  values.coercionErrors = {failedKeys, failedRHS, assignmentConversions};

  let getterCalls = 0;
  let setterCalls = 0;
  Object.defineProperty(window, "__vv8_throwing", {
    configurable: true,
    get() { getterCalls++; throw new Error("expected-getter-error"); },
    set() { setterCalls++; throw new Error("expected-setter-error"); },
  });
  try { void window.__vv8_throwing; } catch (error) { values.getterError = error.message; }
  try { window.__vv8_throwing = 1; } catch (error) { values.setterError = error.message; }
  window.__vv8_recovery = 9;
  values.recovery = window.__vv8_recovery;
  values.exceptionCalls = {getterCalls, setterCalls};

  let reflectGetters = 0;
  let reflectSetters = 0;
  Object.defineProperty(window, "__vv8_reflected", {
    configurable: true,
    get() { reflectGetters++; return this.value; },
    set(value) { reflectSetters++; this.value = value; },
  });
  const explicitReceiver = {value: 10};
  const reflectedValue = Reflect.get(window, "__vv8_reflected", explicitReceiver);
  const reflectedSet = Reflect.set(window, "__vv8_reflected", 20, explicitReceiver);
  values.reflection = {reflectedValue, reflectedSet, receiverValue: explicitReceiver.value,
                       reflectGetters, reflectSetters};
  window.__vv8_reflect_default = 15;
  values.reflectedDefault = Reflect.get(window, "__vv8_reflect_default");
  const numericHost = document.createElement("div");
  Reflect.set(numericHost, 0, 41);
  values.reflectedNumeric = Reflect.get(numericHost, 0);
  values.has = "__vv8_has_in" in window;
  values.reflectHas = Reflect.has(window, "__vv8_has_reflect");

  storage.setItem("engine-dispatch-key", "dispatch-value");
  const getItem = storage.getItem;
  values.dispatch = [
    storage.getItem("engine-dispatch-key"),
    getItem.bind(storage)("engine-dispatch-key"),
    getItem.call(storage, "engine-dispatch-key"),
    getItem.apply(storage, ["engine-dispatch-key"]),
  ];
  const url = new URL("/constructed", "https://example.test/base");
  values.constructedPath = url.pathname;

  sources.direct = 'window.__vv8_eval_direct = 11;\n//# sourceURL=engine-eval-direct.js';
  sources.indirect = 'window.__vv8_eval_indirect = 12;\n//# sourceURL=engine-eval-indirect.js';
  sources.nested = 'window.__vv8_eval_nested = 13;\n//# sourceURL=engine-eval-nested.js';
  sources.outer = "eval(" + JSON.stringify(sources.nested) + ");\n//# sourceURL=engine-eval-outer.js";
  eval(sources.direct);
  (0, eval)(sources.indirect);
  eval(sources.outer);
  sources.functionBody = "host.__vv8_function = 17; return host.__vv8_function;";
  const dynamic = new Function("host", sources.functionBody);
  sources.functionSource = dynamic.toString();
  values.dynamicFunction = dynamic(window);
  values.evaluated = [window.__vv8_eval_direct, window.__vv8_eval_indirect, window.__vv8_eval_nested];

  const ordinary = {};
  void ordinary.__vv8_plain_missing;
  values.excludedBuiltin = Math.abs(-3);
  values.excludedUserFunction = ((number) => number + 1)(2);

  window.__vv8_hot_read = 7;
  window.__vv8_hot_keyed = 5;
  window.__vv8_hot_write = 0;
  storage.setItem("engine-hot-key", "hot-value");
  let readTotal = 0;
  let keyedTotal = 0;
  let lastWrite = 0;
  let methodTotal = 0;
  const hotKey = "__vv8_hot_keyed";
  for (let iteration = 0; iteration < 1000; iteration++) {
    readTotal += window.__vv8_hot_read;
    keyedTotal += window[hotKey];
    lastWrite = ++window.__vv8_hot_write;
    methodTotal += storage.getItem("engine-hot-key").length;
  }
  values.hot = {iterations: 1000, readTotal, keyedTotal, lastWrite, methodTotal};
  values.unicode = window.__vv8_unicode_result;

  const worker = new Worker("engine-worker.js");
  try {
    const response = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Engine worker timed out")), 15000);
      worker.onmessage = event => { clearTimeout(timer); resolve(event.data); };
      worker.onerror = event => { clearTimeout(timer); reject(new Error(event.message)); };
    });
    values.worker = response.values;
    contexts.worker = response.href;
  } finally {
    worker.terminate();
  }

  async function frameResult(origin, kind) {
    const frameURL = new URL("/engine-frame.html", origin);
    frameURL.searchParams.set("kind", kind);
    const iframe = document.createElement("iframe");
    const response = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        window.removeEventListener("message", onMessage);
        reject(new Error("Engine frame timed out: " + kind));
      }, 15000);
      function onMessage(event) {
        if (event.origin === frameURL.origin && event.source === iframe.contentWindow
            && event.data.type === "camoufox-engine-frame" && event.data.kind === kind) {
          clearTimeout(timer);
          window.removeEventListener("message", onMessage);
          resolve(event.data);
        }
      }
      window.addEventListener("message", onMessage);
      iframe.src = frameURL.href;
      document.body.appendChild(iframe);
    });
    iframe.remove();
    return response;
  }
  const crossOrigin = new URL(location.href).searchParams.get("frameOrigin");
  if (!crossOrigin) throw new Error("A second local origin is required");
  values.frames = {};
  for (const [kind, origin] of [["same", location.origin], ["cross", crossOrigin]]) {
    const response = await frameResult(origin, kind);
    values.frames[kind] = response.value;
    contexts[kind] = response.href;
  }
  const originalContextURL = location.href;
  const pushedContextURL = new URL(originalContextURL);
  pushedContextURL.searchParams.set("engineNavigation", "push");
  contexts.navigation = {};
  history.pushState(null, "", pushedContextURL.href);
  window.__vv8_context_push = 101;
  contexts.navigation.push = location.href;
  location.hash = "engine-context";
  window.__vv8_context_hash = 102;
  contexts.navigation.hash = location.href;
  history.replaceState(null, "", originalContextURL);
  window.__vv8_context_restore = 103;
  contexts.navigation.restore = location.href;
  storage.removeItem("engine-dispatch-key");
  storage.removeItem("engine-hot-key");
  storage.removeItem("engine-long-argument");
  document.querySelector("#result").textContent = JSON.stringify({values, sources, contexts});
})().catch(error => {
  document.querySelector("#result").textContent = "Fixture failed: " + error.stack;
});
