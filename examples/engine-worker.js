self.__vv8_worker_value = 19;
const workerValue = self.__vv8_worker_value;
const workerURL = new URL("/worker", "https://example.test/base");
postMessage({values: {value: workerValue, pathname: workerURL.pathname}, href: location.href});
