const frame = document.createElement("iframe");
frame.onload = () => opener.postMessage({type: "camoufox-ccw-ready"}, location.origin);
frame.src = "engine-ccw-frame.html?role=foreign";
document.body.appendChild(frame);
