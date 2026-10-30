(async () => {
  try {
    const result = {};
    window.__ref_store = 1;
    window.__ref_store += 2;
    result.postfix = window.__ref_store++;
    result.prefix = ++window.__ref_store;
    result.reflected = Reflect.get(window, "__ref_store");
    result.reflectSet = Reflect.set(window, "__ref_store", 9);
    result.exists = "__ref_store" in window;

    let keyConversions = 0;
    const key = { toString() { keyConversions++; return "__ref_key"; } };
    window[key] = 17;
    result.keyValue = window[key];
    result.keyConversions = keyConversions;

    // Taking a host method reference is a property observation even before call.
    const beacon = navigator.sendBeacon;
    result.methodReference = typeof beacon;
    result.userAgent = typeof navigator.userAgent;
    result.explicitGetter = typeof Object.getOwnPropertyDescriptor(
      Navigator.prototype, "userAgent").get.call(navigator);

    const create = document.createElement;
    create.call(document, "ref-call");
    create.apply(document, ["ref-apply"]);
    create.bind(document)("ref-bind");
    document.createElement("ref-direct");
    result.urlProtocol = new URL("https://example.test/path").protocol;

    const plain = { refPlainExcluded: 1 };
    result.plainValue = plain.refPlainExcluded;
    const proxy = new Proxy(navigator, { get() { return "proxy-only"; } });
    result.proxyValue = proxy.refProxyExcluded;

    let accessorReads = 0;
    const argument = { text: "colon:slash\\ quote\" 中🦊", nested: [1, 2] };
    Object.defineProperty(argument, "danger", {
      enumerable: true,
      get() { accessorReads++; return "must not be observed"; }
    });
    beacon.call(navigator, "/beacon", argument);
    result.argumentAccessorReads = accessorReads;

    eval("window.__ref_direct_eval = 1;");
    (0, eval)("window.__ref_indirect_eval = 2;");
    new Function("window.__ref_function = 3;")();
    result.dynamicValues = [window.__ref_direct_eval, window.__ref_indirect_eval,
                            window.__ref_function];

    window.__ref_hot = 1;
    let sum = 0;
    for (let i = 0; i < 6000; i++) sum += window.__ref_hot;
    for (let i = 0; i < 4096; i++) document.createElement("ref-hot");
    result.hotSum = sum;

    const worker = new Worker("/worker.js");
    result.worker = await new Promise((resolve, reject) => {
      worker.onmessage = event => resolve(event.data);
      worker.onerror = () => reject(new Error("worker failed"));
    });
    worker.terminate();
    await fetch("/result", { method: "POST", body: JSON.stringify(result) });
    document.body.textContent = "reference completed";
  } catch (error) {
    await fetch("/result", { method: "POST", body: JSON.stringify({ error: String(error) }) });
  }
})();
