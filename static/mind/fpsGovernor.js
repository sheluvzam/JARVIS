// FPS governor — a pure state machine driven by dependency-injected
// callbacks, with no direct reference to the renderer/composer/pool. Keeps
// a rolling 60-frame delta sample; only a *sustained* (3s+) drop below
// 30fps triggers degradation, never a single slow frame (a GC pause is not
// a weak GPU). Two ordered, one-way steps: disable bloom, then disable
// pulses/drift + freeze shimmer/flow uniforms. Each step logs via
// console.info so degradation reads as intentional, not a bug.
const RING_SIZE = 60;
const TARGET_FPS = 30;
const SUSTAIN_MS = 3000;

export class FpsGovernor {
  constructor({ disableBloom, disablePulsesAndDrift, freezeFlowUniforms }) {
    this._disableBloom = disableBloom;
    this._disablePulsesAndDrift = disablePulsesAndDrift;
    this._freezeFlowUniforms = freezeFlowUniforms;

    this._samples = new Float32Array(RING_SIZE);
    this._writeIndex = 0;
    this._sampleCount = 0;
    this._lowSince = null;

    this.state = "OK"; // OK -> BLOOM_OFF -> DEGRADED (one-way ratchet, no auto-recovery)
    this.pulsesEnabled = true;
    this.lastAvgFps = 60;
  }

  recordFrame(deltaSeconds) {
    // Sanity clamp: discard nonsense samples outright (debugger pauses,
    // a backgrounded-tab resume slipping through) so one bad delta can
    // never corrupt the rolling average or masquerade as sustained load.
    if (!(deltaSeconds > 0) || deltaSeconds > 1) return;

    this._samples[this._writeIndex] = deltaSeconds;
    this._writeIndex = (this._writeIndex + 1) % RING_SIZE;
    this._sampleCount = Math.min(this._sampleCount + 1, RING_SIZE);
    if (this._sampleCount < RING_SIZE || this.state === "DEGRADED") return;

    let sum = 0;
    for (let i = 0; i < RING_SIZE; i++) sum += this._samples[i];
    const avgFps = RING_SIZE / sum;
    this.lastAvgFps = avgFps;

    if (avgFps >= TARGET_FPS) {
      this._lowSince = null;
      return;
    }
    const now = performance.now();
    if (this._lowSince === null) this._lowSince = now;
    if (now - this._lowSince < SUSTAIN_MS) return;

    if (this.state === "OK") {
      this.state = "BLOOM_OFF";
      this._lowSince = now; // fresh sustain window for the next threshold
      this._disableBloom();
      console.info(`[mind][perf] avg FPS ${avgFps.toFixed(1)} sustained <${TARGET_FPS} for 3s — disabling bloom`);
    } else if (this.state === "BLOOM_OFF") {
      this.state = "DEGRADED";
      this.pulsesEnabled = false;
      this._disablePulsesAndDrift();
      this._freezeFlowUniforms();
      console.info(
        `[mind][perf] avg FPS ${avgFps.toFixed(1)} still <${TARGET_FPS} after bloom-off — disabling pulses/drift, freezing flow uniforms`
      );
    }
  }
}
