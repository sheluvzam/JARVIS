// Deterministic layout, computed once at load (Tier 4). computeLayout is a
// pure function — its result is stored in state.layout and never
// recomputed in the render loop; "baked once" is enforced by call-site
// placement (scene.js calls this exactly once, right after the skeleton
// fetch resolves), not by a cache flag.
import * as THREE from "three";
import { rngForId } from "./hash.js";

const MEMORY_CENTER = new THREE.Vector3(-34, 2, -6);
const MEMORY_RADIUS = 20;
const TOOL_CENTER = new THREE.Vector3(32, -2, -4);
const TOOL_RADIUS = 12;
const TOOL_ORBIT_RADIUS = 5;
const AGENT_RADIUS = 46;
const AGENT_ARC_DEGREES = 110;
const AGENT_Z_OFFSET = 30;

function fibonacciSpherePoint(i, n) {
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  const y = 1 - (i / Math.max(1, n - 1)) * 2;
  const radiusAtY = Math.sqrt(Math.max(0, 1 - y * y));
  const theta = goldenAngle * i;
  return new THREE.Vector3(Math.cos(theta) * radiusAtY, y, Math.sin(theta) * radiusAtY);
}

/** Same jitter math used for the initial bake, exposed standalone so a
 * live `memory_created` event (Tier 6) can place a single new node in the
 * web without recomputing the whole layout. */
export function layoutSingleMemory(id, recency) {
  const rng = rngForId(id);
  const theta = rng() * Math.PI * 2;
  const phi = Math.acos(2 * rng() - 1);
  const radius = MEMORY_RADIUS * (0.25 + 0.75 * (1 - recency) * rng());
  const offset = new THREE.Vector3(
    radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.sin(phi) * Math.sin(theta),
    radius * Math.cos(phi)
  );
  return MEMORY_CENTER.clone().add(offset);
}

function layoutMemories(memoryList) {
  const memories = new Map();
  memoryList.forEach((memory) => {
    memories.set(memory.id, layoutSingleMemory(memory.id, memory.recency));
  });
  return memories;
}

function layoutAgents(agentList) {
  const agents = new Map();
  const arcRad = (AGENT_ARC_DEGREES * Math.PI) / 180;
  agentList.forEach((agent, i) => {
    const t = agentList.length <= 1 ? 0.5 : i / (agentList.length - 1);
    const angle = -arcRad / 2 + t * arcRad;
    agents.set(
      agent.id,
      new THREE.Vector3(Math.sin(angle) * AGENT_RADIUS, 0, AGENT_Z_OFFSET + Math.cos(angle) * AGENT_RADIUS * 0.35)
    );
  });
  return agents;
}

function layoutTools(toolList, agents) {
  const tools = new Map();
  const centralTools = toolList.filter((tool) => !tool.owner_agent_id);
  const ownedTools = toolList.filter((tool) => tool.owner_agent_id);

  centralTools.forEach((tool, i) => {
    const dir = fibonacciSpherePoint(i, centralTools.length);
    const radius = TOOL_RADIUS * (0.5 + 0.5 * rngForId(tool.id)());
    tools.set(tool.id, TOOL_CENTER.clone().add(dir.multiplyScalar(radius)));
  });

  const ownedByAgent = new Map();
  ownedTools.forEach((tool) => {
    const list = ownedByAgent.get(tool.owner_agent_id) ?? [];
    list.push(tool);
    ownedByAgent.set(tool.owner_agent_id, list);
  });
  ownedByAgent.forEach((toolsForAgent, agentId) => {
    const agentPos = agents.get(agentId);
    if (!agentPos) return; // unknown owner — drop rather than guess a position
    toolsForAgent.forEach((tool, i) => {
      const angle = (i / toolsForAgent.length) * Math.PI * 2;
      const rng = rngForId(tool.id);
      const wobble = 0.85 + rng() * 0.3;
      const pos = agentPos.clone().add(
        new THREE.Vector3(
          Math.cos(angle) * TOOL_ORBIT_RADIUS * wobble,
          (rng() - 0.5) * 3,
          Math.sin(angle) * TOOL_ORBIT_RADIUS * wobble
        )
      );
      tools.set(tool.id, pos);
    });
  });

  return tools;
}

function centroidOf(positionsMap, fallback) {
  const v = new THREE.Vector3();
  let n = 0;
  positionsMap.forEach((p) => {
    v.add(p);
    n++;
  });
  return n > 0 ? v.multiplyScalar(1 / n) : fallback.clone();
}

export function computeLayout(skeleton) {
  const core = new THREE.Vector3(0, 0, 0);
  const agents = layoutAgents(skeleton.nodes.agents);
  const memories = layoutMemories(skeleton.nodes.memories);
  const tools = layoutTools(skeleton.nodes.tools, agents);
  // Real anchor points (the same centroid used for region-label placement),
  // not fictitious nodes — lets the backend emit honest "core -> region"
  // trunk edges without inventing a node that isn't actually rendered.
  const hubs = {
    memories: centroidOf(memories, MEMORY_CENTER),
    tools: centroidOf(tools, TOOL_CENTER),
  };
  return { core, memories, tools, agents, hubs };
}

/** Look up a node's baked world position by id across all regions + core
 * (plus the two region-hub anchor ids used by trunk edges). */
export function resolvePosition(layout, id) {
  if (id === "core") return layout.core;
  if (id === "hub-memories") return layout.hubs.memories;
  if (id === "hub-tools") return layout.hubs.tools;
  return layout.memories.get(id) ?? layout.tools.get(id) ?? layout.agents.get(id) ?? null;
}
