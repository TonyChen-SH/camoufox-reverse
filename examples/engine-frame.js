window.__vv8_frame_value = 23;
parent.postMessage({type: "camoufox-engine-frame",
                    kind: new URL(location.href).searchParams.get("kind"),
                    value: window.__vv8_frame_value,
                    href: location.href}, "*");
