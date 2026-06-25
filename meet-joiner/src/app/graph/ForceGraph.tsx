"use client";

import { useEffect, useMemo, useRef } from "react";
import { colorFor, type GraphEdge, type GraphNode, type Subgraph } from "./types";

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
  onSelect: (node: GraphNode | null) => void;
};

// Self-contained canvas force-directed graph — no external deps.
// Velocity-Verlet-ish integration: repulsion + link springs + centering gravity.
export default function ForceGraph({ data, selectedId, onSelect }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // View transform (world → screen): screen = world * scale + offset.
  const view = useRef({ scale: 1, offsetX: 0, offsetY: 0 });

  // Interaction state kept in refs so the rAF loop sees fresh values.
  const nodesRef = useRef<SimNode[]>([]);
  const edgesRef = useRef<GraphEdge[]>([]);
  const idxRef = useRef<Map<string, SimNode>>(new Map());
  const hoverRef = useRef<string | null>(null);
  const selectedRef = useRef<string | null>(selectedId);
  const dragRef = useRef<{ id: string | null; panning: boolean; lastX: number; lastY: number }>({
    id: null,
    panning: false,
    lastX: 0,
    lastY: 0,
  });
  const onSelectRef = useRef(onSelect);

  // Keep callback/selection refs current without touching them during render.
  useEffect(() => {
    onSelectRef.current = onSelect;
    selectedRef.current = selectedId;
  }, [onSelect, selectedId]);

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
    }

    const radiusOf = (n: SimNode) => 5 + Math.min(10, Math.sqrt(n.deg) * 2.2);

    const toWorld = (sx: number, sy: number) => ({
      x: (sx - view.current.offsetX) / view.current.scale,
      y: (sy - view.current.offsetY) / view.current.scale,
    });

    const pick = (sx: number, sy: number): SimNode | null => {
      const w = toWorld(sx, sy);
      let best: SimNode | null = null;
      let bestD = Infinity;
      for (const n of nodesRef.current) {
        const r = radiusOf(n) + 4;
        const d = (n.x - w.x) ** 2 + (n.y - w.y) ** 2;
        if (d < r * r && d < bestD) {
          best = n;
          bestD = d;
        }
      }
      return best;
    };

    const step = () => {
      const nodes = nodesRef.current;
      const edges = edgesRef.current;
      const idx = idxRef.current;
      const REP = 5200; // repulsion strength
      const SPRING = 0.02; // link stiffness
      const LEN = 90; // ideal link length
      const CENTER = 0.012; // gravity toward origin
      const DAMP = 0.86;

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
      // Centering + integrate.
      const dragId = dragRef.current.id;
      for (const n of nodes) {
        n.vx -= n.x * CENTER;
        n.vy -= n.y * CENTER;
        n.vx *= DAMP;
        n.vy *= DAMP;
        if (n.id === dragId) continue; // pinned to cursor
        n.x += n.vx;
        n.y += n.vy;
      }
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

      // Edges.
      ctx.lineWidth = 1 / scale;
      for (const e of edgesRef.current) {
        const s = idx.get(e.source_node_id);
        const t = idx.get(e.target_node_id);
        if (!s || !t) continue;
        const lit = focus && (e.source_node_id === focus || e.target_node_id === focus);
        ctx.strokeStyle = lit ? "rgba(243,234,211,0.55)" : "rgba(243,234,211,0.12)";
        ctx.beginPath();
        ctx.moveTo(s.x, s.y);
        ctx.lineTo(t.x, t.y);
        ctx.stroke();
      }

      // Nodes.
      for (const n of nodes) {
        const r = radiusOf(n);
        const isFocus = n.id === focus;
        const isAdj = adj.has(n.id);
        const dim = focus && !isFocus && !isAdj ? 0.28 : 1;
        ctx.globalAlpha = dim;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fillStyle = colorFor(n.type);
        ctx.fill();
        if (n.id === selected) {
          ctx.lineWidth = 2.5 / scale;
          ctx.strokeStyle = "#ffffff";
          ctx.stroke();
        }
        // Labels only when zoomed in enough or focused, to avoid clutter.
        if (scale > 0.75 || isFocus || isAdj) {
          ctx.globalAlpha = dim;
          ctx.fillStyle = "rgba(243,234,211,0.9)";
          ctx.font = `${11 / scale}px ui-sans-serif, system-ui, sans-serif`;
          ctx.textAlign = "center";
          const label = n.name.length > 24 ? n.name.slice(0, 23) + "…" : n.name;
          ctx.fillText(label, n.x, n.y + r + 11 / scale);
        }
        ctx.globalAlpha = 1;
      }
      ctx.restore();
    };

    const tick = () => {
      step();
      draw();
      raf = requestAnimationFrame(tick);
    };
    tick();

    // ── Pointer interaction ───────────────────────────────────────
    const localXY = (e: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    };

    const onDown = (e: PointerEvent) => {
      canvas.setPointerCapture(e.pointerId);
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
        return;
      }
      if (drag.panning) {
        view.current.offsetX += x - drag.lastX;
        view.current.offsetY += y - drag.lastY;
        drag.lastX = x;
        drag.lastY = y;
        return;
      }
      const hit = pick(x, y);
      hoverRef.current = hit?.id ?? null;
      canvas.style.cursor = hit ? "pointer" : "grab";
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

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const v = view.current;
      const factor = Math.exp(-e.deltaY * 0.0014);
      const newScale = Math.min(4, Math.max(0.15, v.scale * factor));
      // zoom around cursor
      const wx = (mx - v.offsetX) / v.scale;
      const wy = (my - v.offsetY) / v.scale;
      v.scale = newScale;
      v.offsetX = mx - wx * newScale;
      v.offsetY = my - wy * newScale;
    };

    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", onUp);
    canvas.addEventListener("wheel", onWheel, { passive: false });

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("wheel", onWheel);
    };
  }, []);

  return (
    <div ref={wrapRef} className="relative h-full w-full">
      <canvas ref={canvasRef} className="block h-full w-full touch-none" />
    </div>
  );
}
