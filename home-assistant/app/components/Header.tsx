"use client";

import { useSession, signOut } from "next-auth/react";
import { useRouter } from "next/navigation";
import { ModelSwitcher } from "./ModelSwitcher";

type Props = {
  sessionId?: string;
  currentModel?: string;
  onModelChange?: (model: string) => void;
  onToggleSidebar: () => void;
  sidebarOpen: boolean;
};

export function Header({ sessionId, currentModel, onModelChange, onToggleSidebar, sidebarOpen }: Props) {
  const { data: session } = useSession();
  const router = useRouter();

  return (
    <header className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)] shrink-0">
      <div className="flex items-center gap-3">
        <button
          onClick={onToggleSidebar}
          className="text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors cursor-pointer p-1"
          title={sidebarOpen ? "Close sidebar" : "Open sidebar"}
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
          </svg>
        </button>
        <button
          onClick={() => router.push("/")}
          className="text-sm font-medium text-[var(--color-text-primary)] hover:text-[var(--color-accent)] transition-colors cursor-pointer"
        >
          AI Assistant
        </button>
      </div>

      <div className="flex items-center gap-3">
        {sessionId && currentModel && onModelChange && (
          <ModelSwitcher currentModel={currentModel} onChange={onModelChange} />
        )}
        <span className="text-xs text-[var(--color-text-muted)]">
          {session?.user?.email}
        </span>
        <button
          onClick={() => signOut()}
          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors cursor-pointer"
        >
          Sign out
        </button>
      </div>
    </header>
  );
}
