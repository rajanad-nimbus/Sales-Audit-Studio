import { redirect } from 'next/navigation';

/** Diagnostics has no page of its own: it opens on system health. */
export default function DiagnosticsIndex() {
  redirect('/diagnostics/health');
}
