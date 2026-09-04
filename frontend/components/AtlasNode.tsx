"use client";

import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import type { GraphNode } from "@/types/analysis";

export type AtlasNodeData = { node: GraphNode; slot: number; dim: boolean; selected: boolean };
export type AtlasFlowNode = Node<AtlasNodeData, "atlas">;

/** Package colour comes from a fixed categorical slot; identity is always in the label too. */
const SLOT_BORDER = [
  "border-series-1", "border-series-2", "border-series-3", "border-series-4",
  "border-series-5", "border-series-6", "border-series-7", "border-series-8",
] as const;

export default function AtlasNode({ data }: NodeProps<AtlasFlowNode>) {
  const { node, slot, dim, selected } = data;
  const border = SLOT_BORDER[slot % SLOT_BORDER.length];
  return (
    <div
      className={`w-[180px] rounded-lg border-l-4 bg-surface px-2.5 py-1.5 shadow-sm transition ${border} ${
        selected ? "outline outline-2 outline-accent" : "outline outline-1 outline-edge"
      } ${dim ? "opacity-25" : ""}`}
      title={node.id}
    >
      <Handle type="target" position={Position.Left} className="!h-1.5 !w-1.5 !border-0 !bg-ink-3" />
      <div className="flex items-center gap-1.5">
        <span className="truncate font-mono text-[11px] text-ink">{node.label}</span>
        {node.is_entry_point && <span className="shrink-0 text-[8px] uppercase tracking-wider text-accent">entry</span>}
        {node.is_test && <span className="shrink-0 text-[8px] uppercase tracking-wider text-ink-3">test</span>}
      </div>
      <div className="truncate text-[9px] text-ink-3">
        {node.type === "file"
          ? `${node.package} · ${node.classes}c ${node.functions}f`
          : `${node.type} · ${node.file.split("/").pop()}`}
      </div>
      <Handle type="source" position={Position.Right} className="!h-1.5 !w-1.5 !border-0 !bg-ink-3" />
    </div>
  );
}
