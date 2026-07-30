// Navigation — raycasting, hover highlight, click-to-inspect. Loaded via a
// dynamic import at the bottom of scene.js (see the comment there for why
// this must never be a static import).
import { state, setRegionVisible } from "./scene.js";

const panelEl = document.getElementById("inspector-panel");
const HOVER_EDGE_OPACITY = 0.35;

/** Walks up through every `.parent` to the scene root — three.js's own
 * raycaster only checks a leaf object's own `.visible`, not its ancestor
 * Group's, which is exactly how legend-hidden nodes stay clickable if this
 * check is skipped. */
function isVisibleChain(object3D) {
  let node = object3D;
  while (node) {
    if (!node.visible) return false;
    node = node.parent;
  }
  return true;
}

function candidateMeshes() {
  const meshes = [];
  state.regionMeshHandles.forEach((handle) => {
    if (isVisibleChain(handle.nodeMesh)) meshes.push(handle.nodeMesh);
  });
  if (state.coreMesh && isVisibleChain(state.coreMesh)) meshes.push(state.coreMesh);
  return meshes;
}

function resolveHit(hit) {
  const regionId = hit.object.userData.regionId;
  if (regionId === "core") return { regionId: "core", index: -1, nodeId: "core" };
  const handle = state.regionMeshHandles.get(regionId);
  const nodeId = handle?.ids[hit.instanceId];
  if (!handle || nodeId === undefined) return null;
  return { regionId, index: hit.instanceId, nodeId };
}

function setNodeHighlight(hit, value) {
  if (!hit || hit.regionId === "core" || hit.index === -1) return;
  const handle = state.regionMeshHandles.get(hit.regionId);
  if (!handle) return;
  // Don't stomp an in-flight recall flare fading out at the same index —
  // let updateFlares() keep owning it.
  const flareActive = state.activeFlares.some((f) => f.regionId === hit.regionId && f.index === hit.index);
  if (flareActive && value === 0) return;
  handle.aHighlight[hit.index] = value;
  handle.nodeGeometry.attributes.aHighlight.needsUpdate = true;
}

function setEdgeDim(dimmed) {
  const opacity = dimmed ? HOVER_EDGE_OPACITY : 1.0;
  state.edgeLines.forEach((line) => {
    line.material.uniforms.uOpacity.value = opacity;
  });
}

let hovered = null;

function updateHover(pointerEvent) {
  const rect = state.renderer.domElement.getBoundingClientRect();
  state.pointer.x = ((pointerEvent.clientX - rect.left) / rect.width) * 2 - 1;
  state.pointer.y = -((pointerEvent.clientY - rect.top) / rect.height) * 2 + 1;
  state.raycaster.setFromCamera(state.pointer, state.camera);

  const intersections = state.raycaster.intersectObjects(candidateMeshes(), false);
  const hit = intersections.length > 0 ? resolveHit(intersections[0]) : null;

  const changed = (hit?.nodeId ?? null) !== (hovered?.nodeId ?? null);
  if (!changed) return;

  if (hovered) setNodeHighlight(hovered, 0);
  hovered = hit;
  if (hovered) setNodeHighlight(hovered, 1);
  setEdgeDim(!!hovered);
  state.renderer.domElement.style.cursor = hovered ? "pointer" : "";
}

function describeNode(node) {
  const dl = document.createElement("dl");
  const row = (term, value) => {
    const dt = document.createElement("dt");
    dt.textContent = term;
    const dd = document.createElement("dd");
    dd.textContent = value;
    dl.append(dt, dd);
  };

  if (node.region === "memories") {
    row("Content", node.content_preview);
    row("Tags", node.tags?.join(", ") || "—");
    row("Recency", (node.recency ?? 0).toFixed(2));
    row("Importance", (node.importance ?? 0).toFixed(2));
    row("Created", new Date(node.created_at).toLocaleString());
  } else if (node.region === "tools") {
    row("Category", node.category);
    row("Usage count", String(node.usage_count));
    row("Owner", node.owner_agent_id ?? "shared (central)");
  } else if (node.region === "agents") {
    row("Role", node.role);
    row("Status", node.status);
    row("Activity", (node.activity_level ?? 0).toFixed(2));
    row("Tools", String(node.tool_ids?.length ?? 0));
  } else if (node.region === "core") {
    row("Activity", (node.activity_level ?? 0).toFixed(2));
  }
  return dl;
}

function showInspector(nodeId) {
  const node = state.nodesById.get(nodeId);
  if (!node) return;
  panelEl.innerHTML = "";
  const h2 = document.createElement("h2");
  h2.textContent = node.label;
  panelEl.append(h2, describeNode(node));
  panelEl.hidden = false;
}

function hideInspector() {
  panelEl.hidden = true;
  panelEl.innerHTML = "";
}

function onClick() {
  if (hovered) showInspector(hovered.nodeId);
  else hideInspector();
}

function onPointerLeave() {
  if (hovered) setNodeHighlight(hovered, 0);
  hovered = null;
  setEdgeDim(false);
  state.renderer.domElement.style.cursor = "";
}

export function initInspector() {
  const canvas = state.renderer.domElement;
  canvas.addEventListener("pointermove", updateHover);
  canvas.addEventListener("pointerleave", onPointerLeave);
  canvas.addEventListener("click", onClick);
}

// Re-exported so a future legend UI (or debugging from the console) can
// drive region visibility through the same single source of truth the
// raycast filter reads — see setRegionVisible in scene.js.
export { setRegionVisible };
