"use client";

import { useState } from "react";
import type { ToolCall } from "./ChatMessage";

// Friendly display names for MCP tools
const TOOL_LABELS: Record<string, string> = {
  sonos_get_zones: "Sonos Zones",
  sonos_play: "Sonos Play",
  sonos_pause: "Sonos Pause",
  sonos_volume: "Sonos Volume",
  sonos_favourite: "Sonos Favourite",
  roborock_list_zones: "Vacuum Zones",
  roborock_clean_zone: "Vacuum Clean",
  query_metrics: "Query Metrics",
  list_metrics: "List Metrics",
};

function formatToolName(name: string): string {
  // Strip mcp__home-automation__ prefix
  const short = name.replace(/^mcp__[^_]+__/, "");
  return TOOL_LABELS[short] ?? short;
}

export function ToolCallDisplay({ toolCall }: { toolCall: ToolCall }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-lg bg-[var(--color-tool-bg)] border border-[var(--color-tool-border)] text-xs overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-white/5 transition-colors cursor-pointer"
      >
        <span className="text-[var(--color-accent)]">
          {toolCall.result ? "+" : "~"}
        </span>
        <span className="font-medium text-[var(--color-text-secondary)]">
          {formatToolName(toolCall.name)}
        </span>
        <span className="ml-auto text-[var(--color-text-muted)]">
          {expanded ? "▾" : "▸"}
        </span>
      </button>

      {expanded && (
        <div className="px-3 pb-2 space-y-2 border-t border-[var(--color-tool-border)]">
          <div className="pt-2">
            <div className="text-[var(--color-text-muted)] mb-1">Input:</div>
            <pre className="text-[var(--color-text-secondary)] whitespace-pre-wrap break-words">
              {JSON.stringify(toolCall.input, null, 2)}
            </pre>
          </div>
          {toolCall.result && (
            <div>
              <div className="text-[var(--color-text-muted)] mb-1">Result:</div>
              <pre className="text-[var(--color-text-secondary)] whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
                {toolCall.result}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
