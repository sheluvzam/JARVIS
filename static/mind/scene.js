// Mind visualization — scene bootstrap.
//
// Tier order (see plan): void -> light -> layout -> wiring -> life -> navigation.
// This file owns `state`, the render loop, and the skeleton fetch. It must
// NOT statically import inspector.js — see the dynamic import at the very
// bottom of this file for why (top-level-await + circular static import
// would deadlock module evaluation).
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { starfieldShader, membraneShader, nodeGlowShader, haloShader, edgeShimmerShader, flowCometShader } from "./shaders.js";
import { hashStringToUnitFloat, mulberry32 } from "./hash.js";
import { computeLayout, resolvePosition, layoutSingleMemory } from "./layout.js";
import { PulsePool } from "./pulsePool.js";
import { FpsGovernor } from "./fpsGovernor.js";

const SKELETON_URL = "/api/mind/skeleton";
const LIVE_WS_PATH = "/ws/mind/live";

// Single shared uniform objects, referenced by every material that needs
// them — written once per frame in the render loop (Tier 6), never per
// material. uFlowTime is separate so the FPS governor can freeze
// shimmer/flow motion without freezing node-breathing time.
export const sharedUniforms = { uTime: { value: 0 } };
export const sharedFlow = { uFlowTime: { value: 0 } };

const statsEl = document.getElementById("stats");

/** Shared mutable state, threaded through layout/inspector modules. */
export const state = {
  renderer: null,
  scene: null,
  camera: null,
  controls: null,
  composer: null,
  bloomPass: null,
  cssRenderer: null,
  raycaster: new THREE.Raycaster(),
  pointer: new THREE.Vector2(),
  regionGroups: new Map(), // regionId -> THREE.Group (holds InstancedMesh + label)
  nodeIndex: new Map(), // regionId -> string[] (instanceId -> node id)
  nodesById: new Map(), // node id -> DTO
  layout: null,
  skeleton: null,
  edgeCounts: new Map(), // edge kind -> count
  regionMeshHandles: new Map(), // regionId -> mutable mesh/attribute handle (Tier 6 live growth)
  edgeLineHandles: new Map(), // edge kind -> mutable geometry handle (Tier 6 live growth)
  activeFlares: [], // in-flight recall flares: { regionId, index, start }
  pulsePool: null,
  driftParticles: null,
  fpsGovernor: null,
  pulsesEnabled: true,
  flowFrozen: false,
};

function setStatsError(message) {
  statsEl.textContent = message;
  statsEl.classList.add("error");
}

function buildRenderer() {
  const canvas = document.getElementById("scene-canvas");
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setClearColor(0x050508, 1);
  return renderer;
}

function buildCamera() {
  const camera = new THREE.PerspectiveCamera(50, window.innerWidth / window.innerHeight, 0.1, 2000);
  camera.position.set(0, 42, 108);
  return camera;
}

function buildControls(camera, domElement) {
  const controls = new OrbitControls(camera, domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.06;
  controls.minDistance = 30;
  controls.maxDistance = 260;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.35;
  const stopAutoRotate = () => {
    controls.autoRotate = false;
  };
  domElement.addEventListener("pointerdown", stopAutoRotate, { once: true });
  domElement.addEventListener("wheel", stopAutoRotate, { once: true, passive: true });
  return controls;
}

function buildStarfield() {
  const count = 4000;
  const positions = new Float32Array(count * 3);
  const phases = new Float32Array(count);
  const sizes = new Float32Array(count);
  const rng = mulberry32(0x5eed);
  for (let i = 0; i < count; i++) {
    const radius = 400 + rng() * 400;
    const theta = rng() * Math.PI * 2;
    const phi = Math.acos(2 * rng() - 1);
    positions[i * 3] = radius * Math.sin(phi) * Math.cos(theta);
    positions[i * 3 + 1] = radius * Math.sin(phi) * Math.sin(theta);
    positions[i * 3 + 2] = radius * Math.cos(phi);
    phases[i] = hashStringToUnitFloat(`star-${i}`);
    sizes[i] = 1.0 + rng() * 2.2;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("aPhase", new THREE.BufferAttribute(phases, 1));
  geometry.setAttribute("aSize", new THREE.BufferAttribute(sizes, 1));
  const material = new THREE.ShaderMaterial({
    vertexShader: starfieldShader.vertexShader,
    fragmentShader: starfieldShader.fragmentShader,
    uniforms: {
      uTime: sharedUniforms.uTime,
      uColor: { value: new THREE.Color(0x9d97c7) },
      uOpacity: { value: 1.0 },
    },
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  return new THREE.Points(geometry, material);
}

function buildMembrane() {
  const geometry = new THREE.SphereGeometry(240, 48, 32);
  const material = new THREE.ShaderMaterial({
    vertexShader: membraneShader.vertexShader,
    fragmentShader: membraneShader.fragmentShader,
    uniforms: {
      uColor: { value: new THREE.Color(0x6a3fd6) },
      uOpacity: { value: 0.5 },
      uPower: { value: 2.4 },
    },
    transparent: true,
    depthWrite: false,
    side: THREE.BackSide,
    blending: THREE.AdditiveBlending,
  });
  return new THREE.Mesh(geometry, material);
}

function buildComposer(renderer, scene, camera) {
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloomPass = new UnrealBloomPass(
    new THREE.Vector2(window.innerWidth, window.innerHeight),
    0.85, // strength
    0.65, // radius
    0.18 // threshold — tuned low so emissive node/halo/membrane materials (Tier 3) bloom
  );
  composer.addPass(bloomPass);
  return { composer, bloomPass };
}

function buildCssRenderer() {
  const cssRenderer = new CSS2DRenderer();
  cssRenderer.setSize(window.innerWidth, window.innerHeight);
  const el = cssRenderer.domElement;
  el.style.position = "absolute";
  el.style.top = "0";
  el.style.left = "0";
  el.style.pointerEvents = "none";
  document.getElementById("app").appendChild(el);
  return cssRenderer;
}

function initVoid() {
  const scene = new THREE.Scene();
  const renderer = buildRenderer();
  const camera = buildCamera();
  const controls = buildControls(camera, renderer.domElement);
  const { composer, bloomPass } = buildComposer(renderer, scene, camera);
  const cssRenderer = buildCssRenderer();

  scene.add(buildStarfield());
  scene.add(buildMembrane());

  Object.assign(state, { scene, renderer, camera, controls, composer, bloomPass, cssRenderer });

  const onResize = () => {
    const width = window.innerWidth;
    const height = window.innerHeight;
    renderer.setSize(width, height);
    composer.setSize(width, height);
    cssRenderer.setSize(width, height);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  };
  window.addEventListener("resize", onResize);

  return { onResize };
}

async function fetchSkeleton() {
  let response;
  try {
    response = await fetch(SKELETON_URL);
  } catch (err) {
    setStatsError("failed to load mind — is the server up?");
    throw err;
  }
  if (!response.ok) {
    setStatsError("failed to load mind — is the server up?");
    throw new Error(`skeleton fetch failed: ${response.status} ${response.statusText}`);
  }
  try {
    return await response.json();
  } catch (err) {
    setStatsError("failed to load mind — is the server up?");
    throw err;
  }
}

// --- Tier 4: layout — InstancedMesh per region, merged LineSegments per
// edge kind, CSS2D labels for regions + agents only ---

const NODE_GEOMETRY = new THREE.SphereGeometry(0.55, 10, 8);
const HALO_GEOMETRY = new THREE.PlaneGeometry(1, 1);

function regionColor(skeleton, regionId) {
  const region = skeleton.regions.find((r) => r.id === regionId);
  return new THREE.Color(region ? region.color_hex : "#ffffff");
}

function buildNodeMaterial(color) {
  return new THREE.ShaderMaterial({
    vertexShader: nodeGlowShader.vertexShader,
    fragmentShader: nodeGlowShader.fragmentShader,
    uniforms: { uTime: sharedUniforms.uTime, uColor: { value: color }, uOpacity: { value: 1.0 } },
    transparent: true,
    depthWrite: true,
  });
}

function buildHaloMaterial(color, opacity) {
  return new THREE.ShaderMaterial({
    vertexShader: haloShader.vertexShader,
    fragmentShader: haloShader.fragmentShader,
    uniforms: { uTime: sharedUniforms.uTime, uColor: { value: color }, uOpacity: { value: opacity } },
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
}

// One InstancedMesh (nodes) + one InstancedMesh (halos) per region — two
// draw calls per region regardless of how many nodes it holds. `reserve`
// pre-allocates extra (initially unused) instance slots so a live
// `memory_created` event (Tier 6) can grow the memories region without
// rebuilding its InstancedMesh — InstancedMesh.count is a mutable property
// that limits how many of the allocated instances actually draw, so
// "growing" just means writing into a previously-reserved slot and bumping
// .count.
function buildRegionInstancedMesh(nodeList, positions, color, haloScale, reserve = 0) {
  const count = nodeList.length;
  const capacity = count + reserve;
  const nodeGeometry = NODE_GEOMETRY.clone();
  const haloGeometry = HALO_GEOMETRY.clone();
  const nodeMesh = new THREE.InstancedMesh(nodeGeometry, buildNodeMaterial(color), capacity);
  const haloMesh = new THREE.InstancedMesh(haloGeometry, buildHaloMaterial(color, 0.55), capacity);
  nodeMesh.frustumCulled = false;
  haloMesh.frustumCulled = false;
  nodeMesh.count = count;
  haloMesh.count = count;

  const aPhase = new Float32Array(capacity);
  const aHighlight = new Float32Array(capacity);
  const aRecency = new Float32Array(capacity);
  const aScale = new Float32Array(capacity);
  const matrix = new THREE.Matrix4();
  const ids = [];

  nodeList.forEach((node, i) => {
    const pos = positions.get(node.id);
    matrix.makeTranslation(pos.x, pos.y, pos.z);
    nodeMesh.setMatrixAt(i, matrix);
    haloMesh.setMatrixAt(i, matrix);
    aPhase[i] = hashStringToUnitFloat(node.id);
    aRecency[i] = node.recency ?? 1.0;
    aScale[i] = haloScale;
    ids.push(node.id);
  });
  nodeMesh.instanceMatrix.needsUpdate = true;
  haloMesh.instanceMatrix.needsUpdate = true;

  nodeGeometry.setAttribute("aPhase", new THREE.InstancedBufferAttribute(aPhase, 1));
  nodeGeometry.setAttribute("aHighlight", new THREE.InstancedBufferAttribute(aHighlight, 1));
  nodeGeometry.setAttribute("aRecency", new THREE.InstancedBufferAttribute(aRecency, 1));
  haloGeometry.setAttribute("aPhase", new THREE.InstancedBufferAttribute(aPhase, 1));
  haloGeometry.setAttribute("aScale", new THREE.InstancedBufferAttribute(aScale, 1));

  return { nodeMesh, haloMesh, ids, nodeGeometry, haloGeometry, aPhase, aHighlight, aRecency, aScale, capacity, haloScale };
}

function makeLabelDiv(text, className) {
  const div = document.createElement("div");
  div.className = `mind-label ${className}`;
  div.textContent = text;
  return div;
}

function centroid(positionsMap) {
  const v = new THREE.Vector3();
  let n = 0;
  positionsMap.forEach((p) => {
    v.add(p);
    n++;
  });
  return n > 0 ? v.multiplyScalar(1 / n) : v;
}

function buildCore(skeleton) {
  const color = regionColor(skeleton, "core");
  const core = skeleton.nodes.core;

  const geometry = new THREE.SphereGeometry(2.2, 28, 18);
  const vertexCount = geometry.attributes.position.count;
  // Core has no per-instance attributes (it's a plain Mesh, not
  // InstancedMesh) — fill constant-valued per-vertex attributes instead so
  // it can reuse the same node-glow shader as the instanced regions.
  geometry.setAttribute("aPhase", new THREE.BufferAttribute(new Float32Array(vertexCount).fill(hashStringToUnitFloat("core")), 1));
  geometry.setAttribute("aHighlight", new THREE.BufferAttribute(new Float32Array(vertexCount).fill(0), 1));
  geometry.setAttribute(
    "aRecency",
    new THREE.BufferAttribute(new Float32Array(vertexCount).fill(0.4 + 0.6 * (core.activity_level ?? 1)), 1)
  );
  const mesh = new THREE.Mesh(geometry, buildNodeMaterial(color));
  mesh.name = "core";
  mesh.userData.regionId = "core";

  const haloGeometry = HALO_GEOMETRY.clone();
  const haloVertexCount = haloGeometry.attributes.position.count;
  haloGeometry.setAttribute(
    "aPhase",
    new THREE.BufferAttribute(new Float32Array(haloVertexCount).fill(hashStringToUnitFloat("core-halo")), 1)
  );
  haloGeometry.setAttribute("aScale", new THREE.BufferAttribute(new Float32Array(haloVertexCount).fill(9), 1));
  const halo = new THREE.Mesh(haloGeometry, buildHaloMaterial(color, 0.5));

  const group = new THREE.Group();
  group.name = "region-core";
  group.add(mesh, halo);
  return { group, mesh };
}

// `reserve` pre-allocates extra vertex-pair slots (unused, drawRange
// excludes them) so a live `memory_created` event can append a new
// similarity edge without rebuilding the geometry — same InstancedMesh.count
// trick as buildRegionInstancedMesh, applied to BufferGeometry via
// setDrawRange.
function buildEdgeGeometry(edgeList, layout, reserveEdges = 0) {
  const capacityEdges = edgeList.length + reserveEdges;
  const positions = new Float32Array(capacityEdges * 6);
  const aT = new Float32Array(capacityEdges * 2);
  let rendered = 0;
  edgeList.forEach((edge) => {
    const from = resolvePosition(layout, edge.source_id);
    const to = resolvePosition(layout, edge.target_id);
    if (!from || !to) return; // unresolved endpoint id — drop, never guess a position
    const base = rendered * 6;
    positions[base] = from.x;
    positions[base + 1] = from.y;
    positions[base + 2] = from.z;
    positions[base + 3] = to.x;
    positions[base + 4] = to.y;
    positions[base + 5] = to.z;
    aT[rendered * 2] = 0;
    aT[rendered * 2 + 1] = 1;
    rendered++;
  });
  const geometry = new THREE.BufferGeometry();
  const positionAttr = new THREE.BufferAttribute(positions, 3).setUsage(THREE.DynamicDrawUsage);
  const aTAttr = new THREE.BufferAttribute(aT, 1).setUsage(THREE.DynamicDrawUsage);
  geometry.setAttribute("position", positionAttr);
  geometry.setAttribute("aT", aTAttr);
  geometry.setDrawRange(0, rendered * 2);
  return { geometry, count: rendered, capacityEdges, positions, aT };
}

function buildShimmerLine(edgeList, layout, color, speed, reserveEdges = 0) {
  const built = buildEdgeGeometry(edgeList, layout, reserveEdges);
  const material = new THREE.ShaderMaterial({
    vertexShader: edgeShimmerShader.vertexShader,
    fragmentShader: edgeShimmerShader.fragmentShader,
    uniforms: { uTime: sharedUniforms.uTime, uColor: { value: color }, uOpacity: { value: 1.0 }, uSpeed: { value: speed } },
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  return { ...built, line: new THREE.LineSegments(built.geometry, material) };
}

function buildFlowCometLine(edgeList, layout, color, speed) {
  const built = buildEdgeGeometry(edgeList, layout);
  const material = new THREE.ShaderMaterial({
    vertexShader: flowCometShader.vertexShader,
    fragmentShader: flowCometShader.fragmentShader,
    uniforms: { uFlowTime: sharedFlow.uFlowTime, uColor: { value: color }, uOpacity: { value: 1.0 }, uSpeed: { value: speed } },
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  return { ...built, line: new THREE.LineSegments(built.geometry, material) };
}

const RESERVE_SIMILARITY_EDGE_CAPACITY = 120;

// One merged LineSegments per edge kind — one draw call per kind regardless
// of edge count. Trunk lines get the sharper flow-comet look ("comets of
// light streaming along the trunks"); similarity/agent-tool lines get the
// softer continuous shimmer.
function buildEdgeLines(skeleton, layout) {
  const lines = new Map();
  const counts = new Map();

  const similarity = buildShimmerLine(
    skeleton.edges.memory_similarity,
    layout,
    regionColor(skeleton, "memories"),
    0.35,
    RESERVE_SIMILARITY_EDGE_CAPACITY
  );
  lines.set("memory_similarity", similarity.line);
  counts.set("memory_similarity", similarity.count);
  state.edgeLineHandles.set("memory_similarity", similarity);

  const agentTool = buildShimmerLine(skeleton.edges.agent_tool, layout, regionColor(skeleton, "tools"), 0.5);
  lines.set("agent_tool", agentTool.line);
  counts.set("agent_tool", agentTool.count);

  const trunk = buildFlowCometLine(skeleton.edges.trunk, layout, regionColor(skeleton, "agents"), 0.6);
  lines.set("trunk", trunk.line);
  counts.set("trunk", trunk.count);

  return { lines, counts };
}

const RESERVE_MEMORY_CAPACITY = 40;

const REGION_SPECS = [
  { id: "memories", haloScale: 2.2, labelOffsetY: 14, reserve: RESERVE_MEMORY_CAPACITY },
  { id: "tools", haloScale: 2.0, labelOffsetY: 10, reserve: 0 },
  { id: "agents", haloScale: 3.2, labelOffsetY: 6, reserve: 0 },
];

function buildRegions(skeleton, layout) {
  REGION_SPECS.forEach(({ id, haloScale, labelOffsetY, reserve }) => {
    const nodeList = skeleton.nodes[id];
    const positions = layout[id];
    const color = regionColor(skeleton, id);
    const handle = buildRegionInstancedMesh(nodeList, positions, color, haloScale, reserve);
    const { nodeMesh, haloMesh, ids } = handle;
    state.regionMeshHandles.set(id, handle);
    nodeMesh.userData.regionId = id;
    haloMesh.userData.regionId = id;

    const group = new THREE.Group();
    group.name = `region-${id}`;
    group.userData.regionId = id;
    group.add(nodeMesh, haloMesh);

    const region = skeleton.regions.find((r) => r.id === id);
    if (region) {
      const anchor = centroid(positions).add(new THREE.Vector3(0, labelOffsetY, 0));
      const label = new CSS2DObject(makeLabelDiv(region.label, "region"));
      label.position.copy(anchor);
      group.add(label);
    }

    if (id === "agents") {
      nodeList.forEach((agent) => {
        const pos = positions.get(agent.id);
        if (!pos) return;
        const label = new CSS2DObject(makeLabelDiv(agent.label, "agent"));
        label.position.copy(pos.clone().add(new THREE.Vector3(0, 2.4, 0)));
        group.add(label);
      });
    }

    state.regionGroups.set(id, group);
    state.nodeIndex.set(id, ids);
    state.scene.add(group);
    nodeList.forEach((node) => state.nodesById.set(node.id, { ...node, region: id }));
  });

  const { group: coreGroup, mesh: coreMesh } = buildCore(skeleton);
  state.scene.add(coreGroup);
  state.coreMesh = coreMesh;
  state.nodesById.set("core", { ...skeleton.nodes.core, region: "core" });

  const { lines, counts } = buildEdgeLines(skeleton, layout);
  lines.forEach((line, kind) => {
    line.userData.edgeKind = kind;
    state.scene.add(line);
  });
  state.edgeLines = lines;
  state.edgeCounts = counts;
}

/** Toggles a whole region's InstancedMeshes + label together — this same
 * flag is what Tier 7's raycast visible-chain filter inspects, so legend
 * state and clickability can never drift apart. */
export function setRegionVisible(regionId, visible) {
  const group = state.regionGroups.get(regionId);
  if (group) group.visible = visible;
}

function buildLegend(skeleton) {
  const legendEl = document.getElementById("legend");
  legendEl.innerHTML = "";
  REGION_SPECS.forEach(({ id }) => {
    const region = skeleton.regions.find((r) => r.id === id);
    if (!region) return;
    const count = state.nodeIndex.get(id)?.length ?? 0;

    const row = document.createElement("label");
    row.className = "legend-row";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.addEventListener("change", () => setRegionVisible(id, checkbox.checked));

    const swatch = document.createElement("span");
    swatch.className = "legend-swatch";
    swatch.style.background = region.color_hex;

    const label = document.createElement("span");
    label.textContent = region.label;

    const countEl = document.createElement("span");
    countEl.className = "legend-count";
    countEl.textContent = String(count);

    row.append(checkbox, swatch, label, countEl);
    legendEl.appendChild(row);
  });
}

// Real, live counts — computed from what was actually built/rendered, not
// from the server's (possibly stale or truncated) numbers, so a
// threshold/top-K bug or data truncation is visible at a glance.
function renderedNodeCount() {
  let total = 1; // core
  state.nodeIndex.forEach((ids) => {
    total += ids.length;
  });
  return total;
}

function renderedEdgeCount() {
  let total = 0;
  state.edgeCounts.forEach((count) => {
    total += count;
  });
  return total;
}

function updateStatsLine() {
  const memoriesShown = state.nodeIndex.get("memories")?.length ?? 0;
  const memoriesTotal = state.skeleton.stats.memories_total;
  statsEl.textContent = `${renderedNodeCount()} nodes · ${renderedEdgeCount()} edges · ${memoriesShown}/${memoriesTotal} memories shown`;
  statsEl.classList.remove("error");
}

// --- Tier 6: life ---

function buildDriftParticles() {
  const count = 500;
  const positions = new Float32Array(count * 3);
  const phases = new Float32Array(count);
  const sizes = new Float32Array(count);
  const rng = mulberry32(0xd41f7);
  for (let i = 0; i < count; i++) {
    const radius = 18 + rng() * 85;
    const theta = rng() * Math.PI * 2;
    const phi = Math.acos(2 * rng() - 1);
    positions[i * 3] = radius * Math.sin(phi) * Math.cos(theta);
    positions[i * 3 + 1] = radius * Math.sin(phi) * Math.sin(theta) * 0.4;
    positions[i * 3 + 2] = radius * Math.cos(phi);
    phases[i] = hashStringToUnitFloat(`drift-${i}`);
    sizes[i] = 0.6 + rng() * 1.2;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("aPhase", new THREE.BufferAttribute(phases, 1));
  geometry.setAttribute("aSize", new THREE.BufferAttribute(sizes, 1));
  const material = new THREE.ShaderMaterial({
    vertexShader: starfieldShader.vertexShader,
    fragmentShader: starfieldShader.fragmentShader,
    uniforms: { uTime: sharedUniforms.uTime, uColor: { value: new THREE.Color(0x9b7bff) }, uOpacity: { value: 0.5 } },
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  return new THREE.Points(geometry, material);
}

// Appends one similarity edge into the memory_similarity LineSegments'
// reserved (but so-far-unused) vertex capacity — bumps drawRange rather
// than rebuilding the geometry.
function addSimilarityEdge(sourceId, targetId) {
  const handle = state.edgeLineHandles.get("memory_similarity");
  if (!handle) return false;
  const from = resolvePosition(state.layout, sourceId);
  const to = resolvePosition(state.layout, targetId);
  if (!from || !to) return false; // unresolved endpoint — drop, never guess a position
  if (handle.count >= handle.capacityEdges) {
    console.warn("[mind] similarity-edge pool exhausted — edge not visualized");
    return false;
  }
  const idx = handle.count;
  const base = idx * 6;
  handle.positions[base] = from.x;
  handle.positions[base + 1] = from.y;
  handle.positions[base + 2] = from.z;
  handle.positions[base + 3] = to.x;
  handle.positions[base + 4] = to.y;
  handle.positions[base + 5] = to.z;
  handle.aT[idx * 2] = 0;
  handle.aT[idx * 2 + 1] = 1;
  handle.count++;
  handle.geometry.setDrawRange(0, handle.count * 2);
  handle.geometry.attributes.position.needsUpdate = true;
  handle.geometry.attributes.aT.needsUpdate = true;
  state.edgeCounts.set("memory_similarity", handle.count);
  return true;
}

// Grows the memories InstancedMesh into its reserved capacity for a
// brand-new memory. Returns the new instance index, or null if the
// reserve is exhausted (dropped and logged, never silently faked).
function growMemory(memory) {
  const handle = state.regionMeshHandles.get("memories");
  if (!handle || handle.nodeMesh.count >= handle.capacity) {
    console.warn("[mind] memory pool exhausted — new memory not visualized");
    return null;
  }
  const idx = handle.nodeMesh.count;
  const pos = layoutSingleMemory(memory.id, memory.recency ?? 0.5);
  state.layout.memories.set(memory.id, pos);

  const matrix = new THREE.Matrix4().makeTranslation(pos.x, pos.y, pos.z);
  handle.nodeMesh.setMatrixAt(idx, matrix);
  handle.haloMesh.setMatrixAt(idx, matrix);
  handle.aPhase[idx] = hashStringToUnitFloat(memory.id);
  handle.aRecency[idx] = memory.recency ?? 1.0;
  handle.aScale[idx] = handle.haloScale;
  handle.aHighlight[idx] = 0;
  handle.ids.push(memory.id);

  handle.nodeMesh.count = idx + 1;
  handle.haloMesh.count = idx + 1;
  handle.nodeMesh.instanceMatrix.needsUpdate = true;
  handle.haloMesh.instanceMatrix.needsUpdate = true;
  handle.nodeGeometry.attributes.aPhase.needsUpdate = true;
  handle.nodeGeometry.attributes.aRecency.needsUpdate = true;
  handle.nodeGeometry.attributes.aHighlight.needsUpdate = true;
  handle.haloGeometry.attributes.aPhase.needsUpdate = true;

  state.nodesById.set(memory.id, { ...memory, region: "memories" });
  return idx;
}

const FLARE_DURATION_MS = 900;
const MAX_ACTIVE_FLARES = 32;

/** Briefly brightens a memory node (a visible "it remembered this") and
 * sends a pulse home to the core. Drops silently (with a warning) if the
 * id doesn't resolve — never guesses. */
function flareMemory(memoryId) {
  const handle = state.regionMeshHandles.get("memories");
  const idx = handle ? handle.ids.indexOf(memoryId) : -1;
  if (!handle || idx === -1) {
    console.warn(`[mind] recall event for unknown memory id "${memoryId}" — ignored`);
    return;
  }
  state.activeFlares.push({ regionId: "memories", index: idx, start: performance.now() });
  if (state.activeFlares.length > MAX_ACTIVE_FLARES) state.activeFlares.shift();
  state.pulsePool?.fire(memoryId, "core");
}

function updateFlares(nowMs) {
  if (state.activeFlares.length === 0) return;
  const remaining = [];
  const touched = new Set();
  state.activeFlares.forEach((flare) => {
    const handle = state.regionMeshHandles.get(flare.regionId);
    if (!handle) return;
    const t = (nowMs - flare.start) / FLARE_DURATION_MS;
    if (t >= 1) {
      handle.aHighlight[flare.index] = 0;
    } else {
      handle.aHighlight[flare.index] = 1 - t;
      remaining.push(flare);
    }
    touched.add(flare.regionId);
  });
  touched.forEach((regionId) => {
    state.regionMeshHandles.get(regionId).nodeGeometry.attributes.aHighlight.needsUpdate = true;
  });
  state.activeFlares = remaining;
}

function handleMemoryCreated(payload) {
  const idx = growMemory(payload.memory);
  if (idx === null) return;
  payload.similarity_edges.forEach((edge) => addSimilarityEdge(edge.source_id, edge.target_id));
  state.skeleton.stats.memories_total += 1;
  updateStatsLine();
  buildLegend(state.skeleton);
  state.pulsePool?.fire(payload.memory.id, "core");
}

function handleAgentStatus(payload) {
  const handle = state.regionMeshHandles.get("agents");
  const idx = handle ? handle.ids.indexOf(payload.agent_id) : -1;
  if (!handle || idx === -1) return;
  handle.aRecency[idx] = 0.4 + 0.6 * payload.activity_level;
  handle.nodeGeometry.attributes.aRecency.needsUpdate = true;
  const agent = state.nodesById.get(payload.agent_id);
  if (agent) {
    agent.status = payload.status;
    agent.activity_level = payload.activity_level;
  }
}

// Read-only observer link — never the primary path for anything the page
// needs to function; a dead/reconnecting socket must never slow the scene.
function connectLiveSocket() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}${LIVE_WS_PATH}`);
  ws.addEventListener("message", (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    if (payload.type === "memory_recalled") flareMemory(payload.memory_id);
    else if (payload.type === "memory_created") handleMemoryCreated(payload);
    else if (payload.type === "agent_status") handleAgentStatus(payload);
    // "hello" / "heartbeat" — nothing to do
  });
  ws.addEventListener("close", () => setTimeout(connectLiveSocket, 4000));
  ws.addEventListener("error", () => ws.close());
}

function randomBetween(min, max) {
  return min + Math.random() * (max - min);
}

const AMBIENT_PULSE_INTERVAL_SECONDS = [4, 10];

function disableBloom() {
  state.bloomPass.enabled = false;
}
function disablePulsesAndDrift() {
  state.pulsesEnabled = false;
  if (state.driftParticles) state.driftParticles.visible = false;
}
function freezeFlowUniforms() {
  state.flowFrozen = true;
}

// --- boot ---
initVoid();
const skeleton = await fetchSkeleton();
state.skeleton = skeleton;

const layout = computeLayout(skeleton);
state.layout = layout;
buildRegions(skeleton, layout);
buildLegend(skeleton);
updateStatsLine();

state.driftParticles = buildDriftParticles();
state.scene.add(state.driftParticles);
state.pulsePool = new PulsePool(state.scene, state.layout, 24);
state.fpsGovernor = new FpsGovernor({ disableBloom, disablePulsesAndDrift, freezeFlowUniforms });

connectLiveSocket();

// Debug hook for manual verification (draw-call counts, FPS governor
// state) — see plan verification section. Fine to leave always-on for this
// prototype.
window.__mind = { state };

let nextAmbientAt = performance.now() + randomBetween(...AMBIENT_PULSE_INTERVAL_SECONDS) * 1000;
function maybeFireAmbientPulse(nowMs) {
  if (!state.pulsesEnabled || nowMs < nextAmbientAt) return;
  const memories = state.skeleton.nodes.memories;
  if (memories.length > 0) {
    const pick = memories[Math.floor(Math.random() * memories.length)];
    state.pulsePool.fire(pick.id, "core");
  }
  nextAmbientAt = nowMs + randomBetween(...AMBIENT_PULSE_INTERVAL_SECONDS) * 1000;
}

let lastFrameTime = performance.now();
function animate(nowMs) {
  requestAnimationFrame(animate);
  if (document.hidden) return; // skip the entire frame body on a hidden tab

  const deltaSeconds = (nowMs - lastFrameTime) / 1000;
  lastFrameTime = nowMs;

  sharedUniforms.uTime.value = nowMs / 1000;
  if (!state.flowFrozen) sharedFlow.uFlowTime.value = sharedUniforms.uTime.value;

  state.controls.update();
  updateFlares(nowMs);

  if (state.pulsesEnabled) {
    state.pulsePool.update(deltaSeconds);
    if (state.driftParticles) state.driftParticles.rotation.y += deltaSeconds * 0.02;
    maybeFireAmbientPulse(nowMs);
  }

  state.fpsGovernor.recordFrame(deltaSeconds);

  state.composer.render();
  state.cssRenderer.render(state.scene, state.camera);
}

document.addEventListener("visibilitychange", () => {
  // Belt-and-suspenders alongside fpsGovernor's own delta sanity clamp —
  // never let a huge post-hidden delta reach the rolling average.
  if (!document.hidden) lastFrameTime = performance.now();
});

requestAnimationFrame(animate);

// Loaded dynamically, and only from here — see comment at top of file.
import("./inspector.js").then((mod) => mod.initInspector());
