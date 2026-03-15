"use client";

import { useState, useCallback } from "react";
import type { ToolCall } from "./ChatMessage";

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
  list_metric_labels: "Metric Labels",
  brave_search: "Web Search",
  sheets_list: "List Spreadsheets",
  sheets_read: "Read Spreadsheet",
};

function formatToolName(name: string): string {
  const short = name.replace(/^mcp__[^_]+__/, "");
  return TOOL_LABELS[short] ?? short;
}

function StatusIcon({ status }: { status: "running" | "success" | "error" }) {
  if (status === "running") {
    return (
      <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
        <circle
          cx="12" cy="12" r="10"
          stroke="var(--color-tool-running)"
          strokeWidth="2"
          strokeDasharray="60"
          strokeDashoffset="15"
          strokeLinecap="round"
        />
      </svg>
    );
  }

  if (status === "error") {
    return (
      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="var(--color-tool-error)" strokeWidth="2">
        <circle cx="12" cy="12" r="10" />
        <path d="M15 9l-6 6M9 9l6 6" strokeLinecap="round" />
      </svg>
    );
  }

  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="var(--color-tool-success)" strokeWidth="2">
      <circle cx="12" cy="12" r="10" />
      <path d="M8 12l3 3 5-5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ParamRow({ label, value }: { label: string; value: unknown }) {
  const display = typeof value === "string" ? value : JSON.stringify(value);
  return (
    <div className="flex gap-2 py-0.5">
      <span className="text-[var(--color-text-muted)] shrink-0">{label}:</span>
      <span className="text-[var(--color-text-secondary)] break-all">{display}</span>
    </div>
  );
}

export function ToolCallDisplay({ toolCall }: { toolCall: ToolCall }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  const hasError = toolCall.result?.includes('"error"');
  const status: "running" | "success" | "error" = !toolCall.result
    ? "running"
    : hasError
      ? "error"
      : "success";

  const handleCopy = useCallback(async () => {
    if (!toolCall.result) return;
    await navigator.clipboard.writeText(toolCall.result);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [toolCall.result]);

  const inputEntries = toolCall.input && typeof toolCall.input === "object"
    ? Object.entries(toolCall.input)
    : [];

  return (
    <div className="rounded-lg bg-[var(--color-tool-bg)] border border-[var(--color-tool-border)] text-xs overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-white/5 transition-colors cursor-pointer"
      >
        <StatusIcon status={status} />
        <span className="font-medium text-[var(--color-text-secondary)]">
          {formatToolName(toolCall.name)}
        </span>
        {inputEntries.length > 0 && !expanded && (
          <span className="text-[var(--color-text-muted)] truncate max-w-[200px]">
            {inputEntries.map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ")}
          </span>
        )}
        <span className="ml-auto text-[var(--color-text-muted)] transition-transform duration-200"
              style={{ transform: expanded ? "rotate(180deg)" : "rotate(0deg)" }}>
          ▾
        </span>
      </button>

      <div
        className="transition-all duration-200 ease-in-out overflow-hidden"
        style={{
          maxHeight: expanded ? "500px" : "0",
          opacity: expanded ? 1 : 0,
        }}
      >
        <div className="px-3 pb-2 space-y-2 border-t border-[var(--color-tool-border)]">
          {inputEntries.length > 0 && (
            <div className="pt-2">
              <div className="text-[var(--color-text-muted)] mb-1 font-medium">Parameters</div>
              {inputEntries.map(([key, value]) => (
                <ParamRow key={key} label={key} value={value} />
              ))}
            </div>
          )}
          {toolCall.result && (
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[var(--color-text-muted)] font-medium">Result</span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleCopy();
                  }}
                  className="text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors cursor-pointer px-1"
                >
                  {copied ? "✓ Copied" : "Copy"}
                </button>
              </div>
              <pre className="text-[var(--color-text-secondary)] whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
                {toolCall.result}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
