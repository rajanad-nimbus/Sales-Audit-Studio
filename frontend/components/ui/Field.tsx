interface FieldProps {
  label: string;
  hint?: string;
  children: React.ReactNode;
}

/** Wraps one input, select or textarea with its label. */
export function Field({ label, hint, children }: FieldProps) {
  return (
    <label className="field">
      {label}
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}
