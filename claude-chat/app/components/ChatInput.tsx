"use client";

import { useState, useRef, useEffect } from "react";

type Props = {
  onSend: (message: string) => void;
  disabled: boolean;
};

export function ChatInput({ onSend, disabled }: Props) {
  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!disabled && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [disabled]);

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 200) + "px";
    }
  }, [input]);

  function handleSubmit() {
    const trimmed = input.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setInput("");
  }

  return (
    <div className="border-t border-[var(--color-border)] bg-[var(--color-bg-primary)] p-4">
      <div
        className="max-w-3xl mx-auto flex items-end gap-3 bg-[var(--color-bg-secondary)]
                    border border-[var(--color-border)] rounded-2xl px-4 py-3
                    focus-within:border-[var(--color-border-hover)] transition-colors"
      >
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSubmit();
            }
          }}
          placeholder="Message..."
          disabled={disabled}
          rows={1}
          className="flex-1 bg-transparent text-sm text-[var(--color-text-primary)]
                     placeholder:text-[var(--color-text-muted)] resize-none outline-none
                     disabled:opacity-50"
        />
        <button
          onClick={handleSubmit}
          disabled={disabled || !input.trim()}
          className="shrink-0 p-2 rounded-lg bg-[var(--color-accent)] text-black
                     hover:bg-[var(--color-accent-dim)] disabled:opacity-30
                     disabled:cursor-not-allowed transition-colors cursor-pointer"
        >
          <svg
            className="w-4 h-4"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M6 12L3.27 3.13a1 1 0 011.36-1.24l17.47 8.2a1 1 0 010 1.82l-17.47 8.2a1 1 0 01-1.36-1.24L6 12zm0 0h8"
            />
          </svg>
        </button>
      </div>
      <p className="text-center text-xs text-[var(--color-text-muted)] mt-2">
        Claude can control Sonos, Roborock, and query home metrics.
      </p>
    </div>
  );
}
