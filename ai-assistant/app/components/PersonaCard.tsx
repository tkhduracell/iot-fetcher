"use client";

type Props = {
  id: string;
  name: string;
  description: string;
  icon: string;
  color: string;
  onClick: () => void;
};

export function PersonaCard({ name, description, icon, color, onClick }: Props) {
  return (
    <button
      onClick={onClick}
      className="group flex flex-col items-center gap-3 p-6 rounded-2xl border border-[var(--color-border)]
                 bg-[var(--color-bg-secondary)] hover:border-[var(--color-border-hover)]
                 transition-all cursor-pointer text-center w-full max-w-[220px]"
    >
      <span className="text-4xl">{icon}</span>
      <div>
        <h3
          className="text-sm font-medium mb-1 transition-colors"
          style={{ color }}
        >
          {name}
        </h3>
        <p className="text-xs text-[var(--color-text-muted)] leading-relaxed">
          {description}
        </p>
      </div>
    </button>
  );
}
