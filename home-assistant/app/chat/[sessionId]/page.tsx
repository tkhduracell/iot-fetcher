"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { ChatMessage, type Message, type ToolCall } from "../../components/ChatMessage";
import { ChatInput } from "../../components/ChatInput";

type SessionData = {
  id: string;
  persona: string;
  model: string;
  title: string;
};

export default function ChatSessionPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [sessionData, setSessionData] = useState<SessionData | null>(null);
  const [loading, setLoading] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const hasFetched = useRef(false);

  // Load session data and messages
  useEffect(() => {
    if (hasFetched.current) return;
    hasFetched.current = true;

    async function load() {
      try {
        const [sessionRes, messagesRes] = await Promise.all([
          fetch(`/api/sessions/${sessionId}`),
          fetch(`/api/sessions/${sessionId}/messages`),
        ]);

        if (sessionRes.ok) {
          setSessionData(await sessionRes.json());
        }

        if (messagesRes.ok) {
          const msgs = await messagesRes.json();
          setMessages(
            msgs.map((m: { role: string; content: string; tool_calls: string | null }) => ({
              role: m.role as "user" | "assistant",
              content: m.content,
              toolCalls: m.tool_calls ? JSON.parse(m.tool_calls) : undefined,
            }))
          );
        }
      } catch (err) {
        console.error("Failed to load session:", err);
      } finally {
        setLoading(false);
      }
    }

    load();
  }, [sessionId]);

  // Auto-scroll
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  // Generate title after first assistant response
  const titleGenerated = useRef(false);
  useEffect(() => {
    if (
      titleGenerated.current ||
      !sessionData ||
      sessionData.title ||
      messages.filter((m) => m.role === "assistant" && m.content).length === 0
    ) {
      return;
    }
    titleGenerated.current = true;
    fetch(`/api/sessions/${sessionId}/title`, { method: "POST" }).then(async (res) => {
      if (res.ok) {
        const { title } = await res.json();
        setSessionData((prev) => (prev ? { ...prev, title } : prev));
        // Notify sidebar to refresh
        window.dispatchEvent(new Event("sessions-updated"));
      }
    });
  }, [messages, sessionData, sessionId]);

  const handleSend = useCallback(
    async (text: string) => {
      const userMsg: Message = { role: "user", content: text };
      setMessages((prev) => [...prev, userMsg]);
      setStreaming(true);

      const assistantMsg: Message = { role: "assistant", content: "", toolCalls: [] };
      setMessages((prev) => [...prev, assistantMsg]);

      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text, sessionId }),
        });

        if (!res.ok) {
          const err = await res.text();
          setMessages((prev) => {
            const updated = [...prev];
            updated[updated.length - 1] = { role: "assistant", content: `Error: ${err}` };
            return updated;
          });
          setStreaming(false);
          return;
        }

        const reader = res.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            const data = line.slice(6);
            if (data === "[DONE]") continue;

            try {
              const event = JSON.parse(data);

              if (event.type === "text") {
                setMessages((prev) => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  updated[updated.length - 1] = {
                    ...last,
                    content: last.content + event.text,
                  };
                  return updated;
                });
              } else if (event.type === "tool_use") {
                const tc: ToolCall = {
                  id: event.id,
                  name: event.name,
                  input: event.input,
                };
                setMessages((prev) => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  updated[updated.length - 1] = {
                    ...last,
                    toolCalls: [...(last.toolCalls ?? []), tc],
                  };
                  return updated;
                });
              } else if (event.type === "tool_result") {
                setMessages((prev) => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  const toolCalls = (last.toolCalls ?? []).map((tc) =>
                    tc.id === event.id ? { ...tc, result: event.result } : tc
                  );
                  updated[updated.length - 1] = { ...last, toolCalls };
                  return updated;
                });
              }
            } catch {
              // Skip malformed JSON
            }
          }
        }
      } catch (err) {
        setMessages((prev) => {
          const updated = [...prev];
          updated[updated.length - 1] = {
            role: "assistant",
            content: `Connection error: ${err instanceof Error ? err.message : "Unknown error"}`,
          };
          return updated;
        });
      } finally {
        setStreaming(false);
        // Notify sidebar of new activity
        window.dispatchEvent(new Event("sessions-updated"));
      }
    },
    [sessionId]
  );

  // Get persona-specific suggestions
  const suggestions = getSuggestions(sessionData?.persona);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="flex gap-1">
          <span className="w-2 h-2 rounded-full bg-[var(--color-text-muted)] animate-bounce [animation-delay:0ms]" />
          <span className="w-2 h-2 rounded-full bg-[var(--color-text-muted)] animate-bounce [animation-delay:150ms]" />
          <span className="w-2 h-2 rounded-full bg-[var(--color-text-muted)] animate-bounce [animation-delay:300ms]" />
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto py-6 px-4 space-y-4">
          {messages.length === 0 && (
            <div className="flex items-center justify-center h-[60vh]">
              <div className="text-center space-y-3">
                <p className="text-lg text-[var(--color-text-secondary)]">
                  What can I help you with?
                </p>
                <div className="flex flex-wrap gap-2 justify-center max-w-md">
                  {suggestions.map((suggestion) => (
                    <button
                      key={suggestion}
                      onClick={() => handleSend(suggestion)}
                      className="px-3 py-1.5 text-xs rounded-full border border-[var(--color-border)]
                                 text-[var(--color-text-secondary)] hover:border-[var(--color-border-hover)]
                                 hover:text-[var(--color-text-primary)] transition-colors cursor-pointer"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <ChatMessage key={i} message={msg} />
          ))}

          {streaming && (
            <div className="flex justify-start">
              <div className="flex gap-1 px-4 py-3">
                <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-text-muted)] animate-bounce [animation-delay:0ms]" />
                <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-text-muted)] animate-bounce [animation-delay:150ms]" />
                <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-text-muted)] animate-bounce [animation-delay:300ms]" />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Input */}
      <ChatInput onSend={handleSend} disabled={streaming} persona={sessionData?.persona} />
    </div>
  );
}

function getSuggestions(persona?: string): string[] {
  switch (persona) {
    case "home-assistant":
      return ["What music is playing?", "Vacuum the kitchen", "Show energy usage", "List all metrics"];
    case "researcher":
      return ["What's the weather in Stockholm?", "Latest news about home automation", "Compare Zigbee vs Z-Wave", "Best smart home devices 2025"];
    case "analyst":
      return ["List available spreadsheets", "Show current energy production", "List all metrics", "Show battery status"];
    default:
      return ["What can you do?"];
  }
}
