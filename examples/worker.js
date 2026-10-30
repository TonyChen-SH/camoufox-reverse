const encoder = new TextEncoder();
const url = new URL("/worker", "https://example.test/base");
postMessage({
  userAgent: navigator.userAgent,
  appVersion: navigator.appVersion,
  platform: navigator.platform,
  hardwareConcurrency: navigator.hardwareConcurrency,
  encoding: encoder.encoding,
  encoded: Array.from(encoder.encode("worker-中")),
  pathname: url.pathname,
  canParse: URL.canParse("/worker", "https://example.test/base"),
});
