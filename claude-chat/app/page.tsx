"use client";

import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { PersonaCard } from "./components/PersonaCard";
import { PERSONA_DISPLAY } from "../lib/personas";

export default function HomePage() {
  const router = useRouter();
  const { data: session } = useSession();

  async function handleSelectPersona(personaId: string) {
    const res = await fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona: personaId }),
    });

    if (res.ok) {
      const data = await res.json();
      window.dispatchEvent(new Event("sessions-updated"));
      router.push(`/chat/${data.id}`);
    }
  }

  return (
    <div className="flex items-center justify-center h-full">
      <div className="text-center space-y-8 px-4">
        <div>
          <h1 className="text-2xl font-medium text-[var(--color-text-primary)] mb-2">
            Hi{session?.user?.name ? `, ${session.user.name.split(" ")[0]}` : ""}
          </h1>
          <p className="text-sm text-[var(--color-text-muted)]">
            Choose an assistant to start a conversation
          </p>
        </div>

        <div className="flex flex-wrap gap-4 justify-center">
          {PERSONA_DISPLAY.map((p) => (
            <PersonaCard
              key={p.id}
              {...p}
              onClick={() => handleSelectPersona(p.id)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
