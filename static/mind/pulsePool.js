// Pooled, preallocated pulse sprites — "the agent remembered something."
// All maxPulses sprites are created once at startup and never again; firing
// just claims a free slot. One shared CanvasTexture (radial gradient,
// generated once) backs every sprite.
import * as THREE from "three";
import { resolvePosition } from "./layout.js";

const PULSE_DURATION_SECONDS = 1.3;
const ARC_HEIGHT_RATIO = 0.22;

function createRadialGradientTexture() {
  const size = 128;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  gradient.addColorStop(0, "rgba(255,255,255,1)");
  gradient.addColorStop(0.35, "rgba(216,196,255,0.85)");
  gradient.addColorStop(1, "rgba(138,92,255,0)");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, size, size);
  return new THREE.CanvasTexture(canvas);
}

export class PulsePool {
  constructor(scene, layout, maxPulses = 24) {
    this.layout = layout;
    this.maxPulses = maxPulses;
    this.texture = createRadialGradientTexture();

    this.sprites = new Array(maxPulses);
    this.active = new Array(maxPulses).fill(false);
    this.progress = new Float32Array(maxPulses);
    this.curves = new Array(maxPulses).fill(null);
    this._scratch = new THREE.Vector3();

    for (let i = 0; i < maxPulses; i++) {
      const material = new THREE.SpriteMaterial({
        map: this.texture,
        color: 0xcbb2ff,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        opacity: 0,
      });
      const sprite = new THREE.Sprite(material);
      sprite.visible = false;
      sprite.scale.set(1.8, 1.8, 1.8);
      scene.add(sprite);
      this.sprites[i] = sprite;
    }
  }

  _freeSlot() {
    for (let i = 0; i < this.maxPulses; i++) {
      if (!this.active[i]) return i;
    }
    return -1;
  }

  /** Fires a pulse from one node id to another (usually "core"). Returns
   * false without allocating anything if either id doesn't resolve to a
   * known position, or if the pool is at capacity — pulses never travel
   * toward a default/origin fallback for an unknown id. */
  fire(fromId, toId) {
    const from = resolvePosition(this.layout, fromId);
    const to = resolvePosition(this.layout, toId);
    if (!from || !to) {
      console.warn(`[mind] pulse dropped — unresolved node id (${fromId} -> ${toId})`);
      return false;
    }
    const slot = this._freeSlot();
    if (slot === -1) return false;

    const mid = from.clone().lerp(to, 0.5);
    mid.y += from.distanceTo(to) * ARC_HEIGHT_RATIO;
    const curve = new THREE.QuadraticBezierCurve3(from.clone(), mid, to.clone());

    this.curves[slot] = curve;
    this.progress[slot] = 0;
    this.active[slot] = true;
    const sprite = this.sprites[slot];
    sprite.visible = true;
    curve.getPointAt(0, this._scratch);
    sprite.position.copy(this._scratch);
    sprite.material.opacity = 0;
    return true;
  }

  update(deltaSeconds) {
    for (let i = 0; i < this.maxPulses; i++) {
      if (!this.active[i]) continue;
      this.progress[i] += deltaSeconds / PULSE_DURATION_SECONDS;
      const t = this.progress[i];
      if (t >= 1) {
        this.active[i] = false;
        this.curves[i] = null;
        this.sprites[i].visible = false;
        continue;
      }
      this.curves[i].getPointAt(t, this._scratch);
      const sprite = this.sprites[i];
      sprite.position.copy(this._scratch);
      sprite.material.opacity = Math.sin(Math.PI * t); // fade in, peak mid-flight, fade out
    }
  }
}
