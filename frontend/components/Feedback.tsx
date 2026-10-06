'use client';

import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';

type Tone = 'info' | 'success' | 'error';
interface ToastItem { id: number; tone: Tone; text: string }
const ToastCtx = createContext<(text: string, tone?: Tone) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);
  const dismiss = useCallback((id: number) => setItems((l) => l.filter((t) => t.id !== id)), []);
  const push = useCallback((text: string, tone: Tone = 'info') => {
    const id = ++seq.current;
    setItems((l) => [...l.slice(-3), { id, tone, text }]);
    setTimeout(() => dismiss(id), tone === 'error' ? 8000 : 4500);
  }, [dismiss]);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`}>{t.text}
            <button aria-label="Dismiss" onClick={() => dismiss(t.id)}>×</button></div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function SkeletonRows({ rows = 6, label = 'Loading' }: { rows?: number; label?: string }) {
  return (
    <div className="skeleton-rows" role="status" aria-label={label}>
      {Array.from({ length: rows }, (_, i) => <span key={i} className="skeleton" style={{ width: `${95 - (i % 3) * 12}%` }} />)}
    </div>
  );
}

export function CopyButton({ value, label = 'Copy' }: { value: string; label?: string }) {
  const [done, setDone] = useState(false);
  useEffect(() => { if (!done) return; const t = setTimeout(() => setDone(false), 1500); return () => clearTimeout(t); }, [done]);
  return (
    <button type="button" className="copy-btn" onClick={(e) => { e.stopPropagation(); navigator.clipboard?.writeText(value).then(() => setDone(true)); }}>
      {done ? 'Copied' : label}
    </button>
  );
}
