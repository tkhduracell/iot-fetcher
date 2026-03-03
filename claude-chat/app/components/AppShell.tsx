"use client";

import { useState, useCallback } from "react";
import { useSession } from "next-auth/react";
import { useParams } from "next/navigation";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";

export function AppShell({ children }: { children: React.ReactNode }) {
  const { status } = useSession();
  const params = useParams<{ sessionId?: string }>();
  const sessionId = params?.sessionId;

  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [currentModel, setCurrentModel] = useState("gemini-2.0-flash");

  const handleModelChange = useCallback(
    async (model: string) => {
      if (!sessionId) return;
      setCurrentModel(model);
      await fetch(`/api/sessions/${sessionId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model }),
      });
    },
    [sessionId]
  );

  // Don't show shell for unauthenticated users (sign-in page)
  if (status !== "authenticated") {
    return <>{children}</>;
  }

  return (
    <div className="flex flex-col h-screen">
      <Header
        sessionId={sessionId}
        currentModel={sessionId ? currentModel : undefined}
        onModelChange={sessionId ? handleModelChange : undefined}
        onToggleSidebar={() => setSidebarOpen((o) => !o)}
        sidebarOpen={sidebarOpen}
      />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar open={sidebarOpen} />
        <main className="flex-1 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
