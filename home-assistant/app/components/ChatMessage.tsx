"use client";

import ReactMarkdown from "react-markdown";
import { ToolCallDisplay } from "./ToolCallDisplay";

export type ToolCall = {
  id: string;
  name: string;
  input: Record<string, unknown>;
  result?: string;
};

export type Message = {
  role: "user" | "assistant";
  content: string;
  toolCalls?: ToolCall[];
};

export function ChatMessage({ message }: { message: Message }) {
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-3 ${
          isUser
            ? "bg-[var(--color-user-bg)] border border-[var(--color-user-border)]"
            : "bg-[var(--color-bg-secondary)] border border-[var(--color-border)]"
        }`}
      >
        {message.toolCalls && message.toolCalls.length > 0 && (
          <div className="mb-2 space-y-2">
            {message.toolCalls.map((tc) => (
              <ToolCallDisplay key={tc.id} toolCall={tc} />
            ))}
          </div>
        )}

        {message.content && (
          <div className="prose text-sm text-[var(--color-text-primary)] leading-relaxed">
            <ReactMarkdown>{message.content}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}
