"use client";

const MODELS = [
  { id: "gemini-3-flash", label: "Gemini 3 Flash" },
  { id: "gemini-3.1-pro-preview", label: "Gemini 3.1 Pro" },
  { id: "gemini-3.1-flash-lite", label: "Gemini 3.1 Flash Lite" },
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
