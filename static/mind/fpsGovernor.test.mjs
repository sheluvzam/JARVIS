import { test } from "node:test";
import assert from "node:assert/strict";
import { FpsGovernor } from "./fpsGovernor.js";

function makeGovernor() {
  const calls = { bloom: 0, pulses: 0, freeze: 0 };
  const governor = new FpsGovernor({
    disableBloom: () => calls.bloom++,
    disablePulsesAndDrift: () => calls.pulses++,
    freezeFlowUniforms: () => calls.freeze++,
  });
  return { governor, calls };
}

// Feed RING_SIZE (60) frames at a steady rate. Each recordFrame call after
// the ring first fills re-evaluates the rolling average, so to simulate a
// "sustained" condition across the 3s SUSTAIN_MS window we keep feeding
// frames at that rate past the fill point.
function feedFrames(governor, deltaSeconds, count) {
  for (let i = 0; i < count; i++) governor.recordFrame(deltaSeconds);
}

test("starts in OK state with pulses enabled", () => {
  const { governor } = makeGovernor();
  assert.equal(governor.state, "OK");
  assert.equal(governor.pulsesEnabled, true);
});

test("a single bad delta is discarded and never fills the ring", () => {
  const { governor, calls } = makeGovernor();
  governor.recordFrame(0); // not > 0, discarded
  governor.recordFrame(-1); // negative, discarded
  governor.recordFrame(1.5); // > 1s, discarded
  // Ring never filled, so no averaging/degradation can have happened.
  assert.equal(governor.state, "OK");
  assert.equal(calls.bloom, 0);
});

test("a single slow frame amid good ones does not trip degradation", () => {
  const { governor, calls } = makeGovernor();
  // 59 good frames at 60fps, one bad one, still averages well above 30fps.
  feedFrames(governor, 1 / 60, 59);
  governor.recordFrame(1 / 5); // one slow frame (5fps instant)
  assert.equal(governor.state, "OK");
  assert.equal(calls.bloom, 0);
});

test("sustained sub-30fps for 3s+ transitions OK -> BLOOM_OFF", async () => {
  const { governor, calls } = makeGovernor();
  const slowDelta = 1 / 20; // 20fps, below the 30fps target

  // Fill the ring at the slow rate — this alone doesn't trigger a
  // transition since _lowSince only just got set on this fill.
  feedFrames(governor, slowDelta, 60);
  assert.equal(governor.state, "OK");

  // Real 3s+ must elapse (SUSTAIN_MS is measured via performance.now()),
  // then one more low-average sample should trip the transition.
  await new Promise((resolve) => setTimeout(resolve, 3100));
  governor.recordFrame(slowDelta);

  assert.equal(governor.state, "BLOOM_OFF");
  assert.equal(calls.bloom, 1);
  assert.equal(calls.pulses, 0);
});

test("a second sustained 3s+ low period transitions BLOOM_OFF -> DEGRADED", async () => {
  const { governor, calls } = makeGovernor();
  const slowDelta = 1 / 20;

  feedFrames(governor, slowDelta, 60);
  await new Promise((resolve) => setTimeout(resolve, 3100));
  governor.recordFrame(slowDelta);
  assert.equal(governor.state, "BLOOM_OFF");

  await new Promise((resolve) => setTimeout(resolve, 3100));
  governor.recordFrame(slowDelta);

  assert.equal(governor.state, "DEGRADED");
  assert.equal(governor.pulsesEnabled, false);
  assert.equal(calls.bloom, 1);
  assert.equal(calls.pulses, 1);
  assert.equal(calls.freeze, 1);
});

test("DEGRADED is a one-way ratchet — no auto-recovery on good frames", async () => {
  const { governor, calls } = makeGovernor();
  const slowDelta = 1 / 20;

  feedFrames(governor, slowDelta, 60);
  await new Promise((resolve) => setTimeout(resolve, 3100));
  governor.recordFrame(slowDelta);
  await new Promise((resolve) => setTimeout(resolve, 3100));
  governor.recordFrame(slowDelta);
  assert.equal(governor.state, "DEGRADED");

  // Feed a burst of great frames — state must not revert.
  feedFrames(governor, 1 / 60, 120);
  assert.equal(governor.state, "DEGRADED");
  assert.equal(governor.pulsesEnabled, false);
  assert.equal(calls.bloom, 1);
  assert.equal(calls.pulses, 1);
});

test("good average fps never triggers degradation", () => {
  const { governor, calls } = makeGovernor();
  feedFrames(governor, 1 / 60, 200);
  assert.equal(governor.state, "OK");
  assert.equal(calls.bloom, 0);
});
