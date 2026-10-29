/* Local smoke fixture: use the source lines in the resulting native events. */
(async () => {
  const observed = {
    userAgent: navigator.userAgent,
    appVersion: navigator.appVersion,
    platform: navigator.platform,
    hardwareConcurrency: navigator.hardwareConcurrency,
    screen: [screen.width, screen.height],
  };
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 8;
  const context = canvas.getContext("2d");
  context.fillRect(1, 1, 4, 4);
  observed.pixelType = context.getImageData(-0, 0, 1, 1).constructor.name;
  observed.dataURLLengths = [];
  for (const type of ["image/png", "image/jpeg", "image/webp"]) {
    observed.dataURLLengths.push(canvas.toDataURL(type).length);
  }
  try {
    context.getImageData(0, 0, 0, 0);
  } catch (error) {
    observed.expectedException = error.name;
  }
  observed.afterException = context.getImageData(0, 0, 1, 1).width;
  observed.unicodeDataURLLength = canvas.toDataURL("image/png\u0000中🦊%o %O %j %% %s").length;
  let conversions = 0;
  canvas.toDataURL({toString() { conversions++; return "image/png"; }});
  observed.argumentConversions = conversions;
  for (const kind of ["webgl", "webgl2"]) {
    const gl = document.createElement("canvas").getContext(kind);
    observed[kind] = gl ? gl.getParameter(gl.VERSION) : "unavailable";
  }
  const audio = new AudioBuffer({length: 16, sampleRate: 44100});
  observed.audioLength = audio.getChannelData(0).length;
  audio.copyFromChannel(new Float32Array(4), 0, 1);
  const worker = new Worker("worker.js");
  try {
    observed.worker = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Worker timed out")), 5000);
      worker.onmessage = event => { clearTimeout(timer); resolve(event.data); };
      worker.onerror = event => { clearTimeout(timer); reject(new Error(event.message)); };
    });
  } finally {
    worker.terminate();
  }
  document.querySelector("#result").textContent = JSON.stringify(observed, null, 2);
})().catch(error => {
  document.querySelector("#result").textContent = error.stack;
});
