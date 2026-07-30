import { test } from "node:test";
import assert from "node:assert/strict";
import { hashStringToUint32, hashStringToUnitFloat, mulberry32, rngForId } from "./hash.js";

test("hashStringToUint32 is deterministic for the same input", () => {
  assert.equal(hashStringToUint32("memory-42"), hashStringToUint32("memory-42"));
});

test("hashStringToUint32 differs across distinct inputs", () => {
  assert.notEqual(hashStringToUint32("a"), hashStringToUint32("b"));
});

test("hashStringToUint32 returns an unsigned 32-bit value", () => {
  const h = hashStringToUint32("some-node-id");
  assert.ok(h >= 0 && h <= 0xffffffff);
  assert.equal(h, h >>> 0);
});

test("hashStringToUnitFloat is deterministic and in [0, 1)", () => {
  const a = hashStringToUnitFloat("agent-research");
  const b = hashStringToUnitFloat("agent-research");
  assert.equal(a, b);
  assert.ok(a >= 0 && a < 1);
});

test("mulberry32 produces a deterministic sequence for a fixed seed", () => {
  const seqA = mulberry32(1234);
  const seqB = mulberry32(1234);
  const valuesA = [seqA(), seqA(), seqA()];
  const valuesB = [seqB(), seqB(), seqB()];
  assert.deepEqual(valuesA, valuesB);
});

test("mulberry32 outputs stay in [0, 1)", () => {
  const next = mulberry32(99);
  for (let i = 0; i < 50; i++) {
    const v = next();
    assert.ok(v >= 0 && v < 1);
  }
});

test("mulberry32 diverges across different seeds", () => {
  const a = mulberry32(1)();
  const b = mulberry32(2)();
  assert.notEqual(a, b);
});

test("rngForId is deterministic for the same id", () => {
  const seqA = rngForId("tool-central-3");
  const seqB = rngForId("tool-central-3");
  assert.deepEqual([seqA(), seqA()], [seqB(), seqB()]);
});
