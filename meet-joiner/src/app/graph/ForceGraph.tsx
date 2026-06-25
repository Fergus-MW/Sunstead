"use client";

import { useEffect, useMemo, useRef } from "react";
import {
  colorFor,
  edgeLabel,
  shortLabel,
  typeLabel,
  type GraphEdge,
  type GraphNode,
  type Subgraph,
} from "./types";

type SimNode = GraphNode & {
  x: number;
  y: number;
  vx: number;
  vy: number;
  deg: number;
};

type Props = {
  data: Subgraph;
  selectedId: string | null;
  hiddenTypes: Set<string>;
  onSelect: (node: GraphNode | null) => void;
};

// Self-contained canvas force-directed graph — no external deps.
// Velocity-Verlet-ish integration: repulsion + link springs + centering gravity.
export default function ForceGraph({ data, selectedId, hiddenTypes, onSelect }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);

  // View transform (world → screen): screen = world * scale + offset.
  // `view` is what's drawn; it eases toward `target`. Camera moves are made by
  // setting `target`, which keeps zoom/fit/pan smooth instead of snapping.
  const view = useRef({ scale: 1, offsetX: 0, offsetY: 0 });
  const target = useRef({ scale: 1, offsetX: 0, offsetY: 0 });
  // Frames left during which the camera auto-follows the settling layout.
  const settleRef = useRef(0);
  const hubDegRef = useRef(3);

  // Interaction state kept in refs so the rAF loop sees fresh values.
  const nodesRef = useRef<SimNode[]>([]);
  const edgesRef = useRef<GraphEdge[]>([]);
  const idxRef = useRef<Map<string, SimNode>>(new Map());
  const hoverRef = useRef<string | null>(null);
  const selectedRef = useRef<string | null>(selectedId);
  const hiddenRef = useRef<Set<string>>(hiddenTypes);
  const dragRef = useRef<{ id: string | null; panning: boolean; lastX: number; lastY: number }>({
    id: null,
    panning: false,
    lastX: 0,
    lastY: 0,
  });
  const onSelectRef = useRef(onSelect);
  // Imperative controls, wired up inside the main effect, called from the overlay buttons.
  const apiRef = useRef<{ zoomBy: (f: number) => void; fit: () => void; reset: () => void } | null>(
    null,
  );

  // Keep callback/selection refs current without touching them during render.
  useEffect(() => {
    onSelectRef.current = onSelect;
    selectedRef.current = selectedId;
    hiddenRef.current = hiddenTypes;
  }, [onSelect, selectedId, hiddenTypes]);

  // Stable key so we only rebuild the simulation when the graph actually changes.
  const dataKey = useMemo(
    () => `${data.nodes.map((n) => n.id).join(",")}|${data.edges.length}`,
    [data],
  );

  // (Re)build sim nodes when the data changes.
  useEffect(() => {
    const deg = new Map<string, number>();
    for (const e of data.edges) {
      deg.set(e.source_node_id, (deg.get(e.source_node_id) ?? 0) + 1);
      deg.set(e.target_node_id, (deg.get(e.target_node_id) ?? 0) + 1);
    }
    // Hubs (well-connected nodes) get persistent labels + emphasis so the
    // structure of the graph is legible even when zoomed out.
    const maxDeg = Math.max(1, ...deg.values());
    hubDegRef.current = Math.max(3, Math.ceil(maxDeg * 0.4));
    const prev = idxRef.current;
    const R = 240;
    const sim: SimNode[] = data.nodes.map((n, i) => {
      const old = prev.get(n.id);
      const angle = (i / Math.max(1, data.nodes.length)) * Math.PI * 2;
      return {
        ...n,
        x: old?.x ?? Math.cos(angle) * R + (i % 7) * 4,
        y: old?.y ?? Math.sin(angle) * R + (i % 5) * 4,
        vx: 0,
        vy: 0,
        deg: deg.get(n.id) ?? 0,
      };
    });
    nodesRef.current = sim;
    // keep only edges whose endpoints exist
    const present = new Set(sim.map((n) => n.id));
    edgesRef.current = data.edges.filter(
      (e) => present.has(e.source_node_id) && present.has(e.target_node_id),
    );
    idxRef.current = new Map(sim.map((n) => [n.id, n]));
    // Auto-follow the layout while it settles so the camera glides to frame
    // the whole graph instead of snapping once it's done expanding.
    settleRef.current = 150;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataKey]);

  // Main loop + interaction. Set up once; reads everything from refs.
  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let dpr = 1;
    let cw = 0;
    let ch = 0;

    const resize = () => {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      cw = wrap.clientWidth;
      ch = wrap.clientHeight;
      canvas.width = Math.max(1, Math.floor(cw * dpr));
      canvas.height = Math.max(1, Math.floor(ch * dpr));
      canvas.style.width = `${cw}px`;
      canvas.style.height = `${ch}px`;
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);

    // Center the world the first time we have a size.
    if (view.current.offsetX === 0 && view.current.offsetY === 0) {
      view.current.offsetX = cw / 2;
      view.current.offsetY = ch / 2;
      target.current = { ...view.current };
    }

    // Connected nodes read larger; isolated ones recede so structure stands out.
    const radiusOf = (n: SimNode) =>
      n.deg === 0 ? 3.5 : 5 + Math.min(11, Math.sqrt(n.deg) * 2.4);
    const visible = (n: SimNode) => !hiddenRef.current.has(n.type);

    const toWorld = (sx: number, sy: number) => ({
      x: (sx - view.current.offsetX) / view.current.scale,
      y: (sy - view.current.offsetY) / view.current.scale,
    });

    const pick = (sx: number, sy: number): SimNode | null => {
      const w = toWorld(sx, sy);
      let best: SimNode | null = null;
      let bestD = Infinity;
      for (const n of nodesRef.current) {
        if (!visible(n)) continue;
        const r = radiusOf(n) + 4;
        const d = (n.x - w.x) ** 2 + (n.y - w.y) ** 2;
        if (d < r * r && d < bestD) {
          best = n;
          bestD = d;
        }
      }
      return best;
    };

    // ── Imperative camera controls (used by overlay buttons) ──────
    const fit = () => {
      const ns = nodesRef.current.filter(visible);
      if (!ns.length) return;
      let minX = Infinity;
      let minY = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      for (const n of ns) {
        const r = radiusOf(n) + 16;
        minX = Math.min(minX, n.x - r);
        minY = Math.min(minY, n.y - r);
        maxX = Math.max(maxX, n.x + r);
        maxY = Math.max(maxY, n.y + r);
      }
      const w = maxX - minX || 1;
      const h = maxY - minY || 1;
      const pad = 56;
      const scale = Math.min(1.6, Math.max(0.2, Math.min((cw - pad * 2) / w, (ch - pad * 2) / h)));
      // Set the camera *target*; `view` eases toward it in the loop.
      target.current = {
        scale,
        offsetX: cw / 2 - ((minX + maxX) / 2) * scale,
        offsetY: ch / 2 - ((minY + maxY) / 2) * scale,
      };
    };
    const stopFollow = () => {
      settleRef.current = 0;
    };
    const zoomBy = (factor: number) => {
      stopFollow();
      const t = target.current;
      const newScale = Math.min(4, Math.max(0.15, t.scale * factor));
      // zoom about canvas center
      const wx = (cw / 2 - t.offsetX) / t.scale;
      const wy = (ch / 2 - t.offsetY) / t.scale;
      target.current = {
        scale: newScale,
        offsetX: cw / 2 - wx * newScale,
        offsetY: ch / 2 - wy * newScale,
      };
    };
    const reset = () => {
      hoverRef.current = null;
      settleRef.current = 60; // re-frame with a brief follow
      fit();
    };
    apiRef.current = { zoomBy, fit, reset };

    const step = () => {
      const nodes = nodesRef.current;
      const edges = edgesRef.current;
      const idx = idxRef.current;
      const REP = 3600; // repulsion strength
      const SPRING = 0.025; // link stiffness
      const LEN = 84; // ideal link length
      const CENTER = 0.028; // gravity toward origin — keeps the cloud compact
      const DAMP = 0.82; // lower = energy bleeds off faster, settles sooner

      // Repulsion (O(n^2) — fine for node_limit <= 500).
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 0.01) {
            dx = (i - j) * 0.1 + 0.1;
            dy = 0.1;
            d2 = dx * dx + dy * dy;
          }
          const f = REP / d2;
          const d = Math.sqrt(d2);
          const fx = (dx / d) * f;
          const fy = (dy / d) * f;
          a.vx += fx;
          a.vy += fy;
          b.vx -= fx;
          b.vy -= fy;
        }
      }
      // Springs.
      for (const e of edges) {
        const s = idx.get(e.source_node_id);
        const t = idx.get(e.target_node_id);
        if (!s || !t) continue;
        const dx = t.x - s.x;
        const dy = t.y - s.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const f = (d - LEN) * SPRING;
        const fx = (dx / d) * f;
        const fy = (dy / d) * f;
        s.vx += fx;
        s.vy += fy;
        t.vx -= fx;
        t.vy -= fy;
      }
      // Centering + integrate (with a speed clamp to curb early overshoot).
      const dragId = dragRef.current.id;
      const MAXV = 14;
      for (const n of nodes) {
        n.vx -= n.x * CENTER;
        n.vy -= n.y * CENTER;
        n.vx *= DAMP;
        n.vy *= DAMP;
        const sp = Math.hypot(n.vx, n.vy);
        if (sp > MAXV) {
          n.vx = (n.vx / sp) * MAXV;
          n.vy = (n.vy / sp) * MAXV;
        }
        if (n.id === dragId) continue; // pinned to cursor
        n.x += n.vx;
        n.y += n.vy;
      }
    };

    // Ease the rendered view toward its target each frame; while the layout is
    // settling, keep re-framing so the camera glides out with the graph.
    const ease = () => {
      if (settleRef.current > 0) {
        settleRef.current -= 1;
        fit();
      }
      const v = view.current;
      const t = target.current;
      const k = 0.16;
      v.scale += (t.scale - v.scale) * k;
      v.offsetX += (t.offsetX - v.offsetX) * k;
      v.offsetY += (t.offsetY - v.offsetY) * k;
    };

    const drawArrow = (s: SimNode, t: SimNode, tr: number, alpha: number) => {
      const dx = t.x - s.x;
      const dy = t.y - s.y;
      const d = Math.hypot(dx, dy) || 1;
      const ux = dx / d;
      const uy = dy / d;
      // tip sits just outside the target node
      const tipX = t.x - ux * (tr + 1.5);
      const tipY = t.y - uy * (tr + 1.5);
      const size = 5 / view.current.scale;
      const ax = -uy;
      const ay = ux;
      ctx.globalAlpha = alpha;
      ctx.beginPath();
      ctx.moveTo(tipX, tipY);
      ctx.lineTo(tipX - ux * size + ax * size * 0.55, tipY - uy * size + ay * size * 0.55);
      ctx.lineTo(tipX - ux * size - ax * size * 0.55, tipY - uy * size - ay * size * 0.55);
      ctx.closePath();
      ctx.fill();
      ctx.globalAlpha = 1;
    };

    const draw = () => {
      ctx.save();
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, cw, ch);
      const { scale, offsetX, offsetY } = view.current;
      ctx.translate(offsetX, offsetY);
      ctx.scale(scale, scale);

      const nodes = nodesRef.current;
      const idx = idxRef.current;
      const hover = hoverRef.current;
      const selected = selectedRef.current;
      const focus = hover ?? selected;
      const adj = new Set<string>();
      if (focus) {
        for (const e of edgesRef.current) {
          if (e.source_node_id === focus) adj.add(e.target_node_id);
          if (e.target_node_id === focus) adj.add(e.source_node_id);
        }
      }

      // Edges (skip those touching hidden-type nodes).
      ctx.lineWidth = 1 / scale;
      for (const e of edgesRef.current) {
        const s = idx.get(e.source_node_id);
        const t = idx.get(e.target_node_id);
        if (!s || !t || !visible(s) || !visible(t)) continue;
        const lit = focus && (e.source_node_id === focus || e.target_node_id === focus);
        const dim = focus && !lit ? 0.06 : lit ? 0.55 : 0.12;
        ctx.strokeStyle = `rgba(243,234,211,${dim})`;
        ctx.beginPath();
        ctx.moveTo(s.x, s.y);
        ctx.lineTo(t.x, t.y);
        ctx.stroke();
        // Direction arrow — subtle by default, clear when the edge is lit.
        ctx.fillStyle = `rgba(243,234,211,${lit ? 0.7 : 0.22})`;
        drawArrow(s, t, radiusOf(t), 1);
        // Relationship label on focused edges.
        if (lit && scale > 0.4) {
          ctx.globalAlpha = 1;
          ctx.font = `${10 / scale}px ui-sans-serif, system-ui, sans-serif`;
          ctx.textAlign = "center";
          ctx.lineWidth = 3 / scale;
          ctx.strokeStyle = "rgba(11,26,23,0.9)";
          ctx.fillStyle = "rgba(255,213,122,0.95)";
          const mx = (s.x + t.x) / 2;
          const my = (s.y + t.y) / 2 - 3 / scale;
          ctx.strokeText(edgeLabel(e.type), mx, my);
          ctx.fillText(edgeLabel(e.type), mx, my);
        }
      }

      // Nodes.
      const hubDeg = hubDegRef.current;
      ctx.textAlign = "center";
      ctx.lineJoin = "round";
      for (const n of nodes) {
        if (!visible(n)) continue;
        const r = radiusOf(n);
        const isFocus = n.id === focus;
        const isAdj = adj.has(n.id);
        const isHub = n.deg >= hubDeg;
        // Isolated nodes recede; connected ones stay full strength.
        const connAlpha = n.deg === 0 ? 0.5 : 1;
        const dim = (focus && !isFocus && !isAdj ? 0.18 : 1) * connAlpha;
        const color = colorFor(n.type);
        ctx.globalAlpha = dim;

        // Soft glow behind hubs and the focused node so structure pops.
        if ((isHub || isFocus) && dim > 0.5) {
          ctx.beginPath();
          ctx.arc(n.x, n.y, r + 6 / scale, 0, Math.PI * 2);
          ctx.fillStyle = color;
          ctx.globalAlpha = dim * 0.14;
          ctx.fill();
          ctx.globalAlpha = dim;
        }

        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
        // Thin dark rim separates overlapping nodes — cleaner at any density.
        ctx.lineWidth = 1.5 / scale;
        ctx.strokeStyle = "rgba(11,26,23,0.85)";
        ctx.stroke();
        if (n.id === selected) {
          ctx.lineWidth = 2.5 / scale;
          ctx.strokeStyle = "#ffffff";
          ctx.stroke();
        } else if (isFocus) {
          ctx.lineWidth = 1.5 / scale;
          ctx.strokeStyle = "rgba(255,255,255,0.7)";
          ctx.stroke();
        }

        // Persistent labels for hubs and the focused neighborhood; everything
        // else labels only when zoomed in, to keep the canvas uncluttered.
        if (isHub || isFocus || isAdj || scale > 0.95) {
          const fs = (isHub || isFocus ? 12 : 11) / scale;
          ctx.font = `${isHub ? 600 : 400} ${fs}px ui-sans-serif, system-ui, sans-serif`;
          ctx.globalAlpha = dim;
          // Dark outline makes text legible over edges/nodes without a pill.
          ctx.lineWidth = 3 / scale;
          ctx.strokeStyle = "rgba(11,26,23,0.9)";
          ctx.fillStyle = "rgba(243,234,211,0.95)";
          const label = shortLabel(n.name);
          const ly = n.y + r + fs + 1 / scale;
          ctx.strokeText(label, n.x, ly);
          ctx.fillText(label, n.x, ly);
        }
        ctx.globalAlpha = 1;
      }
      ctx.restore();
    };

    const tick = () => {
      step();
      ease();
      draw();
      raf = requestAnimationFrame(tick);
    };
    tick();

    // ── Pointer interaction ───────────────────────────────────────
    const localXY = (e: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    };

    const showTip = (n: SimNode | null, sx: number, sy: number) => {
      const tip = tipRef.current;
      if (!tip) return;
      if (!n) {
        tip.style.opacity = "0";
        return;
      }
      tip.innerHTML = `<span style="color:${colorFor(
        n.type,
      )}">●</span> <b>${escapeHtml(n.name)}</b><br/><span style="opacity:.6">${escapeHtml(
        typeLabel(n.type),
      )} · ${n.deg} link${n.deg === 1 ? "" : "s"}</span>`;
      tip.style.opacity = "1";
      tip.style.left = `${sx + 14}px`;
      tip.style.top = `${sy + 14}px`;
    };

    const onDown = (e: PointerEvent) => {
      canvas.setPointerCapture(e.pointerId);
      stopFollow(); // user is taking over the camera
      const { x, y } = localXY(e);
      const hit = pick(x, y);
      if (hit) {
        dragRef.current = { id: hit.id, panning: false, lastX: x, lastY: y };
      } else {
        dragRef.current = { id: null, panning: true, lastX: x, lastY: y };
      }
    };

    const onMove = (e: PointerEvent) => {
      const { x, y } = localXY(e);
      const drag = dragRef.current;
      if (drag.id) {
        const node = idxRef.current.get(drag.id);
        if (node) {
          const w = toWorld(x, y);
          node.x = w.x;
          node.y = w.y;
          node.vx = 0;
          node.vy = 0;
        }
        showTip(null, x, y);
        return;
      }
      if (drag.panning) {
        // Pan both view and target so easing doesn't drag against the cursor.
        view.current.offsetX += x - drag.lastX;
        view.current.offsetY += y - drag.lastY;
        target.current.offsetX += x - drag.lastX;
        target.current.offsetY += y - drag.lastY;
        drag.lastX = x;
        drag.lastY = y;
        showTip(null, x, y);
        return;
      }
      const hit = pick(x, y);
      hoverRef.current = hit?.id ?? null;
      canvas.style.cursor = hit ? "pointer" : "grab";
      showTip(hit, x, y);
    };

    const onUp = (e: PointerEvent) => {
      const { x, y } = localXY(e);
      const drag = dragRef.current;
      const moved = Math.abs(x - drag.lastX) + Math.abs(y - drag.lastY) > 4;
      if (drag.id && !moved) {
        const node = idxRef.current.get(drag.id) ?? null;
        onSelectRef.current(node);
      } else if (drag.panning && !moved) {
        onSelectRef.current(null);
      }
      dragRef.current = { id: null, panning: false, lastX: x, lastY: y };
      try {
        canvas.releasePointerCapture(e.pointerId);
      } catch {}
    };

    const onLeave = () => showTip(null, 0, 0);

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      stopFollow();
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const v = view.current;
      const factor = Math.exp(-e.deltaY * 0.0014);
      const newScale = Math.min(4, Math.max(0.15, v.scale * factor));
      // zoom around cursor — apply to the live view and sync the target so the
      // ease loop doesn't pull the zoom back.
      const wx = (mx - v.offsetX) / v.scale;
      const wy = (my - v.offsetY) / v.scale;
      v.scale = newScale;
      v.offsetX = mx - wx * newScale;
      v.offsetY = my - wy * newScale;
      target.current = { ...v };
    };

    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", onUp);
    canvas.addEventListener("pointerleave", onLeave);
    canvas.addEventListener("wheel", onWheel, { passive: false });

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      apiRef.current = null;
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("pointerleave", onLeave);
      canvas.removeEventListener("wheel", onWheel);
    };
  }, []);

  return (
    <div ref={wrapRef} className="relative h-full w-full">
      <canvas ref={canvasRef} className="block h-full w-full touch-none" />
      {/* Floating tooltip */}
      <div
        ref={tipRef}
        className="pointer-events-none absolute z-10 max-w-xs rounded-md border border-[#f3ead3]/15 bg-black/80 px-2.5 py-1.5 text-xs leading-snug text-[#f3ead3] opacity-0 shadow-lg backdrop-blur transition-opacity"
        style={{ left: 0, top: 0 }}
      />
      {/* Camera controls — grouped segmented panel */}
      <div className="absolute bottom-4 right-4 z-10 flex flex-col overflow-hidden rounded-lg border border-[#f3ead3]/12 bg-black/45 text-[#f3ead3]/75 shadow-lg backdrop-blur">
        <CamBtn label="+" title="Zoom in" onClick={() => apiRef.current?.zoomBy(1.25)} />
        <CamBtn label="−" title="Zoom out" onClick={() => apiRef.current?.zoomBy(0.8)} divider />
        <CamBtn label="⤢" title="Fit to view" onClick={() => apiRef.current?.fit()} divider />
        <CamBtn label="⟲" title="Reset view" onClick={() => apiRef.current?.reset()} />
      </div>
    </div>
  );
}

function CamBtn({
  label,
  title,
  onClick,
  divider,
}: {
  label: string;
  title: string;
  onClick: () => void;
  divider?: boolean;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onClick={onClick}
      className={`flex h-9 w-9 items-center justify-center text-base transition-colors hover:bg-[#f3ead3]/10 hover:text-[#f3ead3] ${
        divider ? "border-t border-[#f3ead3]/10" : ""
      }`}
    >
      {label}
    </button>
  );
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
