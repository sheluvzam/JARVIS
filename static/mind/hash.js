// Stable id-hash + seeded PRNG, shared by shaders.js (per-node pulse phase)
// and layout.js (reproducible jitter). Never use Math.random() for anything
// that must look the same across a rebuild of the same node.

/** FNV-1a 32-bit hash, unsigned. */
export function hashStringToUint32(id) {
  let h = 0x811c9dc5;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** FNV-1a hash normalized to [0, 1). */
export function hashStringToUnitFloat(id) {
  return hashStringToUint32(id) / 4294967296;
}

/** mulberry32 seeded PRNG — returns a () => number in [0, 1) generator. */
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function next() {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Convenience: a seeded PRNG bound to a stable string id. */
export function rngForId(id) {
  return mulberry32(hashStringToUint32(id));
}
