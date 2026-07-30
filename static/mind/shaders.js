// ~6 small custom GLSL ShaderMaterials that carry the whole look: node glow,
// halo, edge shimmer, flow comet, starfield, membrane fresnel.
//
// Every fragment shader exposes a `uOpacity` uniform multiplied into final
// alpha — THREE.ShaderMaterial ignores `.opacity`, so inspector.js drives
// hover-dimming through this uniform instead.
//
// None of these declare `attribute mat4 instanceMatrix` themselves — three.js
// auto-injects it (and the USE_INSTANCING macro) when a material is assigned
// to an InstancedMesh; declaring it manually is a compile error.

export const nodeGlowShader = {
  vertexShader: /* glsl */ `
    attribute float aPhase;
    attribute float aHighlight;
    attribute float aRecency;
    uniform float uTime;
    varying float vGlow;
    varying float vHighlight;

    void main() {
      float pulse = 0.5 + 0.5 * sin(uTime * 1.6 + aPhase * 6.28318530718);
      vGlow = mix(0.55, 1.0, pulse) * mix(0.65, 1.0, aRecency);
      vHighlight = aHighlight;
      vec3 pos = position * (1.0 + 0.07 * pulse);
      #ifdef USE_INSTANCING
        gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(pos, 1.0);
      #else
        gl_Position = projectionMatrix * modelViewMatrix * vec4(pos, 1.0);
      #endif
    }
  `,
  fragmentShader: /* glsl */ `
    precision highp float;
    uniform vec3 uColor;
    uniform float uOpacity;
    varying float vGlow;
    varying float vHighlight;

    void main() {
      vec3 color = uColor * vGlow;
      color = mix(color, vec3(1.0), vHighlight * 0.55);
      gl_FragColor = vec4(color, uOpacity);
    }
  `,
};

// Billboarded quad with an analytic radial falloff — deliberately not a
// sphere shell, which has a hard geometric silhouette edge and reads as a
// flat disk from most viewing angles.
export const haloShader = {
  vertexShader: /* glsl */ `
    attribute float aPhase;
    attribute float aScale;
    uniform float uTime;
    varying vec2 vUv;

    void main() {
      vUv = uv;
      #ifdef USE_INSTANCING
        vec4 worldCenter = modelMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0);
      #else
        vec4 worldCenter = modelMatrix * vec4(0.0, 0.0, 0.0, 1.0);
      #endif
      vec3 cameraRight = vec3(viewMatrix[0][0], viewMatrix[1][0], viewMatrix[2][0]);
      vec3 cameraUp = vec3(viewMatrix[0][1], viewMatrix[1][1], viewMatrix[2][1]);
      float pulse = 0.85 + 0.15 * sin(uTime * 1.2 + aPhase * 6.28318530718);
      float scale = aScale * pulse;
      vec3 worldPos = worldCenter.xyz + (cameraRight * position.x + cameraUp * position.y) * scale;
      gl_Position = projectionMatrix * viewMatrix * vec4(worldPos, 1.0);
    }
  `,
  fragmentShader: /* glsl */ `
    precision highp float;
    uniform vec3 uColor;
    uniform float uOpacity;
    varying vec2 vUv;

    void main() {
      float dist = length(vUv - vec2(0.5));
      float falloff = pow(smoothstep(0.5, 0.0, dist), 1.6);
      gl_FragColor = vec4(uColor, falloff * uOpacity);
    }
  `,
};

// Merged LineSegments per edge kind. `aT` (0..1 position along each line) is
// baked into the geometry once at build time (layout.js).
export const edgeShimmerShader = {
  vertexShader: /* glsl */ `
    attribute float aT;
    varying float vT;

    void main() {
      vT = aT;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: /* glsl */ `
    precision highp float;
    uniform vec3 uColor;
    uniform float uOpacity;
    uniform float uTime;
    uniform float uSpeed;
    varying float vT;

    void main() {
      float band = fract(vT * 3.0 - uTime * uSpeed);
      float highlight = smoothstep(0.85, 1.0, band) + smoothstep(0.15, 0.0, band);
      float alpha = (0.16 + highlight * 0.8) * uOpacity;
      gl_FragColor = vec4(uColor, alpha);
    }
  `,
};

// Sharper moving highlight than edgeShimmer — the continuous idle "comets of
// light" on the trunk lines. Driven by uFlowTime, a separate shared uniform
// from uTime so the FPS governor can freeze flow motion without freezing
// node breathing.
export const flowCometShader = {
  vertexShader: /* glsl */ `
    attribute float aT;
    varying float vT;

    void main() {
      vT = aT;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: /* glsl */ `
    precision highp float;
    uniform vec3 uColor;
    uniform float uOpacity;
    uniform float uFlowTime;
    uniform float uSpeed;
    varying float vT;

    void main() {
      float phase = fract(vT - uFlowTime * uSpeed);
      float head = pow(1.0 - smoothstep(0.0, 0.1, phase), 6.0);
      float alpha = (0.05 + head) * uOpacity;
      gl_FragColor = vec4(uColor, alpha);
    }
  `,
};

export const starfieldShader = {
  vertexShader: /* glsl */ `
    attribute float aPhase;
    attribute float aSize;
    uniform float uTime;
    varying float vTwinkle;

    void main() {
      vTwinkle = 0.5 + 0.5 * sin(uTime * 0.6 + aPhase * 6.28318530718);
      vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
      gl_PointSize = aSize * (300.0 / -mvPosition.z);
      gl_Position = projectionMatrix * mvPosition;
    }
  `,
  fragmentShader: /* glsl */ `
    precision highp float;
    uniform vec3 uColor;
    uniform float uOpacity;
    varying float vTwinkle;

    void main() {
      float dist = length(gl_PointCoord - vec2(0.5));
      if (dist > 0.5) discard;
      float falloff = smoothstep(0.5, 0.0, dist);
      gl_FragColor = vec4(uColor, falloff * (0.35 + vTwinkle * 0.65) * uOpacity);
    }
  `,
};

// BackSide sphere enclosing the whole scene. Fresnel rim uses abs(dot(...)),
// not max(dot(...), 0.0) — max() zeroes out the wrong hemisphere from the
// inside of a BackSide sphere and produces a filled/solid disk instead of a
// thin glowing rim at the silhouette edge.
export const membraneShader = {
  vertexShader: /* glsl */ `
    varying vec3 vNormal;
    varying vec3 vViewPosition;

    void main() {
      vNormal = normalize(normalMatrix * normal);
      vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
      vViewPosition = -mvPosition.xyz;
      gl_Position = projectionMatrix * mvPosition;
    }
  `,
  fragmentShader: /* glsl */ `
    precision highp float;
    uniform vec3 uColor;
    uniform float uOpacity;
    uniform float uPower;
    varying vec3 vNormal;
    varying vec3 vViewPosition;

    void main() {
      vec3 viewDir = normalize(vViewPosition);
      float rim = abs(dot(viewDir, normalize(vNormal)));
      float fresnel = pow(1.0 - rim, uPower);
      gl_FragColor = vec4(uColor, fresnel * uOpacity);
    }
  `,
};
