"use strict";

const ionParameters = {
  warmupBatches: 2,
  warmupIterations: 5000,
  measuredIterations: 10000,
  spins: 512,
  pauseMs: 250,
};
const ionScriptURL = document.currentScript.src;

// Arithmetic keeps these synchronous functions sampleable without extra host calls.
function ionRead(iterations, key) {
  let total = 0;
  let checksum = 0;
  const spins = ionParameters.spins;
  for (let i = 0; i < iterations; i++) {
    total += window[key];
    for (let j = 0; j < spins; j++) {
      checksum = (checksum * 1664525 + 1013904223) | 0;
    }
  }
  return [total, checksum];
}

function ionWrite(iterations, key) {
  let checksum = 0;
  const spins = ionParameters.spins;
  for (let i = 0; i < iterations; i++) {
    window[key] = i;
    for (let j = 0; j < spins; j++) {
      checksum = (checksum * 1664525 + 1013904223) | 0;
    }
  }
  return [iterations - 1, checksum];
}

function ionCall(iterations, storage) {
  let total = 0;
  let checksum = 0;
  const spins = ionParameters.spins;
  for (let i = 0; i < iterations; i++) {
    total += storage.getItem("engine-ion-native").length;
    for (let j = 0; j < spins; j++) {
      checksum = (checksum * 1664525 + 1013904223) | 0;
    }
  }
  return [total, checksum];
}

(async function runIonProfile() {
  const storage = window.localStorage;
  storage.setItem("engine-ion-native", "ion-value");
  window.__vv8_ion_read = 7;
  window.__vv8_ion_write = -1;
  const warmup = [];
  window.__vv8_ion_phase = "warmup-start";
  for (let batch = 0; batch < ionParameters.warmupBatches; batch++) {
    warmup.push({
      read: ionRead(ionParameters.warmupIterations, "__vv8_ion_read"),
      write: ionWrite(ionParameters.warmupIterations, "__vv8_ion_write"),
      call: ionCall(ionParameters.warmupIterations, storage),
    });
    // This fixed yield permits off-thread compilation; samples decide whether Ion ran.
    await new Promise(resolve => setTimeout(resolve, ionParameters.pauseMs));
  }
  window.__vv8_ion_phase = "warmup-end";
  await new Promise(resolve => setTimeout(resolve, ionParameters.pauseMs));
  window.__vv8_ion_phase = "measure-start";
  const measured = {
    read: ionRead(ionParameters.measuredIterations, "__vv8_ion_read"),
    write: ionWrite(ionParameters.measuredIterations, "__vv8_ion_write"),
    call: ionCall(ionParameters.measuredIterations, storage),
  };
  window.__vv8_ion_phase = "measure-end";
  const values = {
    warmup, measured,
    writtenReadback: window.__vv8_ion_write,
    storedReadback: storage.getItem("engine-ion-native"),
  };
  document.getElementById("result").textContent = JSON.stringify({
    scriptURL: ionScriptURL, parameters: ionParameters, values,
  });
})().catch(error => {
  document.getElementById("result").textContent = "Fixture failed: " + (error.stack || error);
});
