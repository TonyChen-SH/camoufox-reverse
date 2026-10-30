if (new URL(location.href).searchParams.get("role") === "control") {
  window.__vv8_ccw_control_window = 11;
  navigator.__vv8_ccw_control_navigator = 13;
  document.body.__vv8_ccw_control_element = 17;
} else {
  window.__vv8_ccw_foreign_window = 41;
  navigator.__vv8_ccw_foreign_navigator = 43;
  document.body.__vv8_ccw_foreign_element = 47;
  localStorage.setItem("engine-ccw-native-key", "ccw-value");
  let traps = 0;
  window.__vv8_ccw_plain_proxy = new Proxy({__vv8_ccw_plain: 53}, {
    get(target, key, receiver) { traps++; return Reflect.get(target, key, receiver); },
  });
  window.__vv8_ccw_proxy_count = () => traps;
}
