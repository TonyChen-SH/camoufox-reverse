window.runEngineCrossCompartmentCheck = async function() {
  const control = document.createElement("iframe");
  const loaded = new Promise((resolve, reject) => {
    control.onload = resolve;
    control.onerror = () => reject(new Error("CCW control frame failed"));
  });
  control.src = "engine-ccw-frame.html?role=control";
  document.body.appendChild(control);
  await loaded;
  const local = control.contentWindow;
  const values = {control: [local.__vv8_ccw_control_window,
                           local.navigator.__vv8_ccw_control_navigator,
                           local.document.body.__vv8_ccw_control_element]};
  const contexts = {control: local.location.href};
  const button = document.querySelector("#ccw-run");
  return new Promise((resolve, reject) => {
    button.onclick = async () => {
      button.disabled = true;
      let popup = null;
      let onMessage;
      let timer;
      const ready = new Promise((accept, fail) => {
        timer = setTimeout(() => fail(new Error("CCW popup frame timed out")), 15000);
        onMessage = event => {
          if (event.source === popup && event.origin === location.origin
              && event.data.type === "camoufox-ccw-ready") accept();
        };
        window.addEventListener("message", onMessage);
      });
      popup = window.open("engine-ccw-popup.html", "camoufox-engine-ccw");
      try {
        if (!popup) throw new Error("CCW popup was blocked despite the user gesture");
        await ready;
        const foreign = popup.frames[0];
        const foreignNavigator = foreign.navigator;
        const foreignElement = foreign.document.body;
        const foreignStorage = foreign.localStorage;
        values.foreign = [foreign.__vv8_ccw_foreign_window,
                          foreignNavigator.__vv8_ccw_foreign_navigator,
                          foreignElement.__vv8_ccw_foreign_element];
        foreign.__vv8_ccw_foreign_window = 42;
        foreignNavigator.__vv8_ccw_foreign_navigator = 44;
        foreignElement.__vv8_ccw_foreign_element = 48;
        values.foreignAfter = [foreign.__vv8_ccw_foreign_window,
                               foreignNavigator.__vv8_ccw_foreign_navigator,
                               foreignElement.__vv8_ccw_foreign_element];
        const foreignReference = foreignStorage.getItem;
        values.methodReference = typeof foreignReference;
        values.borrowed = Storage.prototype.getItem.call(foreignStorage, "engine-ccw-native-key");
        values.foreignCall = foreignStorage.getItem("engine-ccw-native-key");
        values.plainProxy = foreign.__vv8_ccw_plain_proxy.__vv8_ccw_plain;
        values.proxyTrapCount = foreign.__vv8_ccw_proxy_count();
        contexts.foreign = foreign.location.href;
        resolve({values, contexts});
      } catch (error) {
        reject(error);
      } finally {
        clearTimeout(timer);
        window.removeEventListener("message", onMessage);
        if (popup) popup.close();
        control.remove();
      }
    };
    button.disabled = false;
  });
};
