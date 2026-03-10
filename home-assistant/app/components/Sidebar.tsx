"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter, useParams } from "next/navigation";

type Session = {
  id: string;
  persona: string;
  title: string;
  model: string;
  created_at: string;
  updated_at: string;
};

type GroupedSessions = {
  label: string;
  sessions: Session[];
};

function groupByDate(sessions: Session[]): GroupedSessions[] {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const weekAgo = new Date(today.getTime() - 7 * 86400000);

  const groups: Record<string, Session[]> = {
    Today: [],
    Yesterday: [],
    "This week": [],
    Older: [],
  };

  for (const s of sessions) {
    const d = new Date(s.updated_at + "Z");
    if (d >= today) groups["Today"].push(s);
    else if (d >= yesterday) groups["Yesterday"].push(s);
    else if (d >= weekAgo) groups["This week"].push(s);
    else groups["Older"].push(s);
  }

  return Object.entries(groups)
    .filter(([, sessions]) => sessions.length > 0)
    .map(([label, sessions]) => ({ label, sessions }));
}

const PERSONA_ICONS: Record<string, string> = {
  "home-assistant": "🏠",
  researcher: "🔍",
  analyst: "📊",
};

export function Sidebar({ open }: { open: boolean }) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const router = useRouter();
  const params = useParams<{ sessionId?: string }>();
  const activeId = params?.sessionId;

  const fetchSessions = useCallback(async () => {
    try {
      const res = await fetch("/api/sessions");
      if (res.ok) {
        setSessions(await res.json());
      }
    } catch (err) {
      console.error("Failed to fetch sessions:", err);
    }
  }, []);

  useEffect(() => {
    fetchSessions();

    const handler = () => fetchSessions();
    window.addEventListener("sessions-updated", handler);
    return () => window.removeEventListener("sessions-updated", handler);
  }, [fetchSessions]);

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    await fetch(`/api/sessions/${id}`, { method: "DELETE" });
    setSessions((prev) => prev.filter((s) => s.id !== id));
    if (activeId === id) {
      router.push("/");
    }
  };

  const grouped = groupByDate(sessions);

  return (
    <aside
      className="shrink-0 border-r border-[var(--color-border)] bg-[var(--color-sidebar-bg)] overflow-y-auto transition-all duration-200"
      style={{ width: open ? "260px" : "0", opacity: open ? 1 : 0 }}
    >
      <div className="p-3" style={{ minWidth: "260px" }}>
        <button
          onClick={() => router.push("/")}
          className="w-full px-3 py-2 text-sm text-left rounded-lg border border-[var(--color-border)]
                     text-[var(--color-text-secondary)] hover:border-[var(--color-border-hover)]
                     hover:text-[var(--color-text-primary)] transition-colors cursor-pointer mb-3"
        >
          + New chat
        </button>

        {grouped.map((group) => (
          <div key={group.label} className="mb-3">
            <div className="text-xs text-[var(--color-text-muted)] px-3 py-1 font-medium">
              {group.label}
            </div>
            {group.sessions.map((s) => (
              <button
                key={s.id}
                onClick={() => router.push(`/chat/${s.id}`)}
                className={`w-full group flex items-start gap-2 px-3 py-2 text-xs rounded-lg transition-colors cursor-pointer
                  ${activeId === s.id
                    ? "bg-[var(--color-bg-tertiary)] text-[var(--color-text-primary)]"
                    : "text-[var(--color-text-secondary)] hover:bg-[var(--color-sidebar-hover)]"
                  }`}
              >
                <span className="shrink-0 mt-0.5">{PERSONA_ICONS[s.persona] ?? "💬"}</span>
                <span className="flex-1 min-w-0 text-left">
                  <span className="block truncate">
                    {s.title || "New chat"}
                  </span>
                  <span className="flex gap-1 mt-0.5">
                    <span className="inline-block px-1.5 py-0.5 rounded text-[10px] leading-tight bg-[var(--color-bg-tertiary)] text-[var(--color-text-muted)]">
                      {s.persona}
                    </span>
                    <span className="inline-block px-1.5 py-0.5 rounded text-[10px] leading-tight bg-[var(--color-bg-tertiary)] text-[var(--color-text-muted)]">
                      {s.model.replace("gemini-", "").replace("-preview", "")}
                    </span>
                  </span>
                </span>
                <span
                  onClick={(e) => handleDelete(s.id, e)}
                  className="shrink-0 mt-0.5 opacity-0 group-hover:opacity-100 text-[var(--color-text-muted)]
                             hover:text-[var(--color-tool-error)] transition-all cursor-pointer"
                  title="Delete"
                >
                  ×
                </span>
              </button>
            ))}
          </div>
        ))}

        {sessions.length === 0 && (
          <p className="text-xs text-[var(--color-text-muted)] px-3 py-4 text-center">
            No conversations yet
          </p>
        )}
      </div>
    </aside>
  );
}
