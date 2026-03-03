"use client";

const MODELS = [
  { id: "gemini-2.0-flash", label: "Gemini 2.0 Flash" },
  { id: "gemini-2.5-flash", label: "Gemini 2.5 Flash" },
  { id: "gemini-2.5-pro", label: "Gemini 2.5 Pro" },
];

type Props = {
  currentModel: string;
  onChange: (model: string) => void;
};

export function ModelSwitcher({ currentModel, onChange }: Props) {
  return (
    <select
      value={currentModel}
      onChange={(e) => onChange(e.target.value)}
      className="text-xs bg-[var(--color-bg-tertiary)] text-[var(--color-text-secondary)]
                 border border-[var(--color-border)] rounded-lg px-2 py-1
                 hover:border-[var(--color-border-hover)] transition-colors cursor-pointer
                 outline-none"
    >
      {MODELS.map((m) => (
        <option key={m.id} value={m.id}>
          {m.label}
        </option>
      ))}
    </select>
  );
}
