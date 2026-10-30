/* Exercise distinct generated WebIDL paths without making external requests. */
export async function runGenericProbe() {
  const observed = {};
  document.title = "trace-title-direct";
  observed.directTitle = document.title;
  observed.reflectSet = Reflect.set(document, "title", "trace-title-reflected");
  observed.reflectedTitle = Reflect.get(document, "title");

  const canvas = document.createElement("canvas");
  canvas.width = 17;
  observed.canvasWidth = Reflect.get(canvas, "width");
  const element = document.createElement("div");
  element.id = "trace-element";
  observed.elementId = element.id;
  element.style.color = "rgb(12, 34, 56)";
  element.style.backgroundColor = "rgb(78, 90, 123)";
  observed.colors = [element.style.color, element.style.backgroundColor];

  const parser = new DOMParser();
  const parsed = parser.parseFromString("<p>parsed-value</p>", "text/html");
  observed.parsedText = parsed.body.textContent;

  const encoder = new TextEncoder();
  observed.encoding = encoder.encoding;
  observed.encoded = Array.from(encoder.encode("中🦊"));
  const blob = new Blob(["payload"], {type: "text/plain"});
  observed.blob = {size: blob.size, type: blob.type};
  const objectURL = URL.createObjectURL(blob);
  try {
    observed.blobProtocol = new URL(objectURL).protocol;
  } finally {
    URL.revokeObjectURL(objectURL);
  }
  observed.canParse = URL.canParse("/path", "https://example.test/base");
  try {
    new URL("not-an-absolute-url");
  } catch (error) {
    observed.constructorException = error.name;
  }
  let conversions = 0;
  const url = new URL({toString() {
    conversions++;
    return "https://example.test/original";
  }});
  url.pathname = "/changed";
  observed.pathname = url.pathname;
  observed.constructorConversions = conversions;

  Storage.prototype.setItem.call(localStorage, "camoufox-trace-key", "stored-value");
  try {
    observed.storedValue = Storage.prototype.getItem.apply(localStorage, ["camoufox-trace-key"]);
  } finally {
    localStorage.removeItem("camoufox-trace-key");
  }
  const permissionPromise = navigator.permissions.query({name: "geolocation"});
  observed.permissionPromise = permissionPromise instanceof Promise;
  observed.permissionCompleted = typeof (await permissionPromise).state === "string";

  const peer = new RTCPeerConnection({iceServers: []});
  observed.peerState = peer.signalingState;
  peer.close();
  observed.peerClosed = peer.signalingState;
  return observed;
}
