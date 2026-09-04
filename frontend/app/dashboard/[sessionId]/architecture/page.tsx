"use client";

import { use, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import dagre from "@dagrejs/dagre";
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import AtlasNode, { type AtlasFlowNode } from "@/components/AtlasNode";
import { ErrorPanel, LoadingPanel } from "@/components/LoadStates";
import { ApiError, getArchitecture } from "@/lib/api";
import type { ArchitectureGraph, GraphNode, Relation } from "@/types/analysis";

const NODE_W = 180;
const NODE_H = 44;
const RELATIONS: Relation[] = ["imports", "calls", "inherits", "contains"];
const RELATION_STYLE: Record<Relation, { stroke: string; dash?: string; label: string }> = {
  imports: { stroke: "var(--color-series-1)", label: "imports" },
  calls: { stroke: "var(--color-series-2)", dash: "6 4", label: "calls" },
  inherits: { stroke: "var(--color-series-3)", label: "inherits" },
  contains: { stroke: "var(--color-ink-3)", dash: "2 4", label: "contains" },
};
const nodeTypes = { atlas: AtlasNode };

function layout(nodes: GraphNode[], edges: ArchitectureGraph["edges"]): Map<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", nodesep: 22, ranksep: 110, marginx: 24, marginy: 24 });
  nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }));
  edges.forEach((e) => {
    if (e.source !== e.target) g.setEdge(e.source, e.target);
  });
  dagre.layout(g);
  const positions = new Map<string, { x: number; y: number }>();
  nodes.forEach((n) => {
    const p = g.node(n.id);
    positions.set(n.id, { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 });
  });
  return positions;
}

function GraphView({ sessionId }: { sessionId: string }) {
  const [graph, setGraph] = useState<ArchitectureGraph | null>(null);
  const [error, setError] = useState<{ message: string; status: number } | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [depth, setDepth] = useState(1);
  const [relations, setRelations] = useState<Set<Relation>>(new Set(RELATIONS));
  const [packageFilter, setPackageFilter] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { fitView } = useReactFlow();

  const load = useCallback(
    (focusId: string | null, d: number) => {
      setGraph(null);
      setError(null);
      getArchitecture(sessionId, focusId ? { focus: focusId, depth: d } : {})
        .then((g) => {
          setGraph(g);
          setSelectedId(focusId);
        })
        .catch((err) =>
          setError(err instanceof ApiError ? { message: err.message, status: err.status } : { message: "Failed to load the graph.", status: -1 }),
        );
    },
    [sessionId],
  );

  useEffect(() => {
    let initial: string | null = null;
    try {
      initial = new URLSearchParams(window.location.search).get("focus");
    } catch {
      /* ignore */
    }
    Promise.resolve().then(() => {
      setFocus(initial);
      load(initial, 1);
    });
  }, [load]);

  const packageSlots = useMemo(() => {
    const map = new Map<string, number>();
    graph?.packages.forEach((p, i) => map.set(p, i));
    return map;
  }, [graph]);

  const { flowNodes, flowEdges } = useMemo(() => {
    if (!graph) return { flowNodes: [] as AtlasFlowNode[], flowEdges: [] as Edge[] };
    const needle = search.trim().toLowerCase();
    const visibleNodes = graph.nodes.filter((n) => !packageFilter || n.package === packageFilter);
    const visibleIds = new Set(visibleNodes.map((n) => n.id));
    const visibleEdges = graph.edges.filter((e) => relations.has(e.relation) && visibleIds.has(e.source) && visibleIds.has(e.target));
    const positions = layout(visibleNodes, visibleEdges);
    const neighbours = new Set<string>();
    if (selectedId) {
      visibleEdges.forEach((e) => {
        if (e.source === selectedId) neighbours.add(e.target);
        if (e.target === selectedId) neighbours.add(e.source);
      });
    }
    const flowNodes: AtlasFlowNode[] = visibleNodes.map((n) => ({
      id: n.id,
      type: "atlas",
      position: positions.get(n.id) ?? { x: 0, y: 0 },
      data: {
        node: n,
        slot: packageSlots.get(n.package) ?? 0,
        selected: n.id === selectedId,
        dim: needle
          ? !(n.id.toLowerCase().includes(needle) || n.label.toLowerCase().includes(needle))
          : selectedId !== null && n.id !== selectedId && !neighbours.has(n.id),
      },
    }));
    const flowEdges: Edge[] = visibleEdges.map((e) => {
      const style = RELATION_STYLE[e.relation];
      const touching = selectedId !== null && (e.source === selectedId || e.target === selectedId);
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        type: "default",
        animated: touching && e.relation === "calls",
        label: e.count > 1 ? `×${e.count}` : undefined,
        labelStyle: { fill: "var(--color-ink-3)", fontSize: 9 },
        labelBgStyle: { fill: "var(--color-page)" },
        style: {
          stroke: style.stroke,
          strokeWidth: touching ? 2 : 1.2,
          strokeDasharray: style.dash,
          opacity: selectedId !== null && !touching ? 0.25 : 0.9,
        },
        markerEnd: { type: MarkerType.ArrowClosed, color: style.stroke, width: 14, height: 14 },
      };
    });
    return { flowNodes, flowEdges };
  }, [graph, relations, packageFilter, search, selectedId, packageSlots]);

  useEffect(() => {
    if (flowNodes.length === 0) return;
    const timer = setTimeout(() => fitView({ padding: 0.15, duration: 300 }), 50);
    return () => clearTimeout(timer);
  }, [flowNodes.length, focus, fitView]);

  if (error) return <ErrorPanel message={error.message} gone={error.status === 404} />;

  const selected = graph?.nodes.find((n) => n.id === selectedId) ?? null;
  const selectedEdges = selected ? graph!.edges.filter((e) => e.source === selected.id || e.target === selected.id) : [];

  function toggleRelation(r: Relation) {
    setRelations((prev) => {
      const next = new Set(prev);
      if (next.has(r)) next.delete(r);
      else next.add(r);
      return next;
    });
  }

  function focusOn(id: string | null, d = depth) {
    setFocus(id);
    setPackageFilter(null);
    setSearch("");
    load(id, d);
    try {
      const url = new URL(window.location.href);
      if (id) url.searchParams.set("focus", id);
      else url.searchParams.delete("focus");
      window.history.replaceState(null, "", url.toString());
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col">
      <header className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Architecture</h1>
          <p className="mt-1 text-sm text-ink-2">
            {focus ? (
              <>
                Neighbourhood of <span className="font-mono">{focus}</span> ·{" "}
                <button onClick={() => focusOn(null)} className="text-accent hover:underline">back to file view</button>
              </>
            ) : (
              "File-level dependency map: resolved imports, calls and inheritance between source files."
            )}
          </p>
        </div>
        {graph && (
          <p className="text-xs text-ink-3">
            {graph.stats.shown_nodes}/{graph.stats.total_nodes} nodes · {graph.stats.shown_edges} edges
            {graph.stats.truncated && <span className="ml-2 text-status-warning">· cut to the most connected</span>}
          </p>
        )}
      </header>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search nodes"
          aria-label="Search nodes"
          className="w-52 rounded-md border border-edge bg-surface px-2 py-1 font-mono text-xs text-ink outline-none placeholder:text-ink-3 focus:border-accent/60"
        />
        {RELATIONS.map((r) => (
          <label key={r} className="flex cursor-pointer items-center gap-1.5 rounded-md border border-edge bg-surface px-2 py-1 text-xs text-ink-2">
            <input type="checkbox" checked={relations.has(r)} onChange={() => toggleRelation(r)} className="accent-accent" />
            <span className="inline-block h-0.5 w-4" style={{ background: RELATION_STYLE[r].stroke }} />
            {RELATION_STYLE[r].label}
          </label>
        ))}
        {graph && graph.packages.length > 1 && (
          <select
            value={packageFilter ?? ""}
            onChange={(e) => setPackageFilter(e.target.value || null)}
            aria-label="Filter by package"
            className="rounded-md border border-edge bg-surface px-2 py-1 text-xs text-ink-2"
          >
            <option value="">All packages</option>
            {graph.packages.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        )}
        {focus && (
          <select
            value={depth}
            onChange={(e) => {
              const d = Number(e.target.value);
              setDepth(d);
              focusOn(focus, d);
            }}
            aria-label="Neighbourhood depth"
            className="rounded-md border border-edge bg-surface px-2 py-1 text-xs text-ink-2"
          >
            <option value={1}>depth 1</option>
            <option value={2}>depth 2</option>
            <option value={3}>depth 3</option>
          </select>
        )}
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-h-[420px] overflow-hidden rounded-xl border border-edge bg-page">
          {!graph ? (
            <LoadingPanel />
          ) : flowNodes.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-ink-3">No source files with relationships to draw.</div>
          ) : (
            <ReactFlow
              nodes={flowNodes}
              edges={flowEdges}
              nodeTypes={nodeTypes}
              colorMode="dark"
              fitView
              minZoom={0.1}
              maxZoom={2}
              nodesDraggable
              nodesConnectable={false}
              elementsSelectable
              onNodeClick={(_, node) => setSelectedId(node.id === selectedId ? null : node.id)}
              onPaneClick={() => setSelectedId(null)}
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={24} color="var(--color-line)" />
              <Controls showInteractive={false} />
              <MiniMap pannable zoomable nodeColor={() => "var(--color-raised)"} maskColor="rgba(13,13,13,0.7)" />
              <Panel position="bottom-left" className="rounded-md border border-edge bg-surface/90 px-2 py-1 text-[10px] text-ink-3">
                {graph.note}
              </Panel>
            </ReactFlow>
          )}
        </div>

        <aside className="min-h-0 overflow-y-auto rounded-xl border border-edge bg-surface p-4">
          {selected ? (
            <>
              <p className="text-[10px] uppercase tracking-wider text-ink-3">{selected.type}</p>
              <h2 className="mt-1 break-all font-mono text-sm font-semibold">{selected.id}</h2>
              <dl className="mt-3 grid grid-cols-[5.5rem_1fr] gap-y-1 text-xs">
                <dt className="text-ink-3">Package</dt><dd className="font-mono text-ink-2">{selected.package}</dd>
                {selected.language && (<><dt className="text-ink-3">Language</dt><dd className="text-ink-2">{selected.language}</dd></>)}
                {selected.start_line != null && (<><dt className="text-ink-3">Lines</dt><dd className="tabular-nums text-ink-2">{selected.start_line}–{selected.end_line}</dd></>)}
                {selected.type === "file" && (<><dt className="text-ink-3">Contents</dt><dd className="text-ink-2">{selected.classes} classes · {selected.functions} functions</dd></>)}
                <dt className="text-ink-3">Degree</dt><dd className="tabular-nums text-ink-2">{selected.degree}</dd>
              </dl>
              {selected.docstring && <p className="mt-3 text-xs leading-relaxed text-ink-2">{selected.docstring.split("\n")[0]}</p>}
              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                <button onClick={() => focusOn(selected.id)} className="rounded-lg bg-accent px-3 py-1.5 font-medium text-white transition hover:bg-accent/85">
                  Focus here
                </button>
                <Link href={`/dashboard/${sessionId}/impact?target=${encodeURIComponent(selected.id)}`} className="rounded-lg border border-edge px-3 py-1.5 text-ink-2 transition hover:bg-raised">
                  Impact analysis
                </Link>
              </div>
              <h3 className="mt-4 text-[10px] uppercase tracking-wider text-ink-3">Relationships · {selectedEdges.length}</h3>
              <ul className="mt-1 space-y-1">
                {selectedEdges.map((e) => {
                  const outgoing = e.source === selected.id;
                  const other = outgoing ? e.target : e.source;
                  return (
                    <li key={e.id} className="flex items-baseline gap-1.5 text-[11px]">
                      <span className="shrink-0" style={{ color: RELATION_STYLE[e.relation].stroke }}>
                        {outgoing ? `${e.relation} →` : `← ${e.relation}`}
                      </span>
                      <button onClick={() => setSelectedId(other)} className="min-w-0 truncate text-left font-mono text-ink-2 hover:text-accent" title={other}>
                        {other}
                      </button>
                      {e.count > 1 && <span className="shrink-0 text-ink-3">×{e.count}</span>}
                    </li>
                  );
                })}
              </ul>
            </>
          ) : (
            <div className="text-xs leading-relaxed text-ink-3">
              <p>Click a node to inspect it. Drag to pan, scroll to zoom.</p>
              <p className="mt-2">Colour = package (see the left border), edge colour = relationship. Entry points and tests are labelled.</p>
              <p className="mt-2">&ldquo;Focus here&rdquo; opens the entity-level neighbourhood of a node, which is how to explore large repositories without drawing everything.</p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

export default function ArchitecturePage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = use(params);
  return (
    <ReactFlowProvider>
      <GraphView sessionId={sessionId} />
    </ReactFlowProvider>
  );
}
