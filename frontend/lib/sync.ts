'use client';

import { useEffect, useRef } from 'react';

// Every successful write (approve, bulk action, batch run, close store day...) announces itself here,
// so counts, badges and lists on every mounted screen refresh together instead of on their own timers.
const EVENT = 'nimbus:data-changed';
let timer: ReturnType<typeof setTimeout> | undefined;

export function notifyDataChanged() {
  if (typeof window === 'undefined') return;
  clearTimeout(timer);
  timer = setTimeout(() => window.dispatchEvent(new Event(EVENT)), 350); // coalesce bursts
}

/** Run `cb` whenever data changes anywhere in the app. */
export function useDataChanged(cb: () => void) {
  const ref = useRef(cb);
  ref.current = cb;
  useEffect(() => {
    const h = () => ref.current();
    window.addEventListener(EVENT, h);
    return () => window.removeEventListener(EVENT, h);
  }, []);
}

/** Load now, then keep fresh: on an interval while the tab is visible, when the tab regains focus, and on any data change. */
export function useLive(cb: () => void, intervalMs: number) {
  const ref = useRef(cb);
  ref.current = cb;
  useEffect(() => {
    const run = () => { if (!document.hidden) ref.current(); };
    ref.current();
    const t = setInterval(run, intervalMs);
    document.addEventListener('visibilitychange', run);
    window.addEventListener(EVENT, run);
    return () => { clearInterval(t); document.removeEventListener('visibilitychange', run); window.removeEventListener(EVENT, run); };
  }, [intervalMs]);
}
