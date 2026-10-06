'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { SkeletonRows, useToast } from '@/components/Feedback';

interface Policy { id: string; dataset: string; retain_days: number; archive_before_purge: boolean }
interface Run { id: string; dataset: string; eligible_records: number; archived_records: number; purged_records: number; status: string; created_at: string }
const apiError = (e: unknown, fallback: string) => (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;

export default function RetentionPage() {
  const toast = useToast();
  const [policies, setPolicies] = useState<Policy[] | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [p, r] = await Promise.all([api.get('/api/operations/retention'), api.get('/api/operations/retention/runs')]);
      setPolicies(p.data); setRuns(r.data); setError(null);
    } catch (e) { setError(apiError(e, 'Could not load retention policies.')); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const preview = async (dataset: string) => {
    try {
      const { data } = await api.get(`/api/operations/retention/${dataset}/preview`);
      setNote(`${dataset}: ${data.eligible_records} records are eligible after ${new Date(data.cutoff).toLocaleDateString()}. This is a preview. Nothing is removed.`);
    } catch (e) { setError(apiError(e, 'Could not preview this policy.')); }
  };
  const save = async (p: Policy, days: number) => {
    setBusy(true);
    try { await api.put(`/api/operations/retention/${p.dataset}`, { retain_days: days, archive_before_purge: p.archive_before_purge }); toast('Retention policy saved.', 'success'); await load(); }
    catch (e) { toast(apiError(e, 'Could not save the policy.'), 'error'); }
    finally { setBusy(false); }
  };
  const archive = async (p: Policy) => {
    setBusy(true);
    try { await api.post(`/api/operations/retention/${p.dataset}/run`, { confirm_purge: false }); toast('Eligible records archived. Purging needs explicit confirmation through the API.', 'success'); await load(); }
    catch (e) { toast(apiError(e, 'Could not archive.'), 'error'); }
    finally { setBusy(false); }
  };

  return (
    <section className="info-card">
      <h2>Data retention</h2>
      <p className="hint" style={{ marginBottom: '.75rem' }}>How long each dataset is kept. Policies are previewed safely. Purging is a separate, supervised step.</p>
      {error && <div className="alert alert-error" role="alert">{error}<button className="btn-secondary btn-sm alert-close" onClick={() => setError(null)} aria-label="Dismiss">×</button></div>}
      {note && <div className="alert alert-info" role="status">{note}<button className="btn-secondary btn-sm alert-close" onClick={() => setNote(null)} aria-label="Dismiss">×</button></div>}
      {!policies ? <SkeletonRows rows={3} label="Loading retention policies" /> : policies.length === 0 ? (
        <p className="cc-empty">No retention policies are configured. IT can add policies through the operations API.</p>
      ) : (
        <div className="table-scroll"><table className="data-table">
          <thead><tr><th>Dataset</th><th className="num">Retain (days)</th><th>Archive first</th><th /></tr></thead>
          <tbody>{policies.map((p) => (
            <tr key={p.id}><td className="font-medium">{p.dataset}</td>
              <td className="num"><input aria-label={`${p.dataset} retention days`} type="number" min={30} defaultValue={p.retain_days} style={{ width: '6rem' }}
                onBlur={(e) => { const d = Number(e.target.value); if (d >= 30 && d !== p.retain_days) save(p, d); }} /></td>
              <td>{p.archive_before_purge ? 'Yes' : 'No'}</td>
              <td><div className="action-buttons"><button className="btn-secondary btn-sm" onClick={() => preview(p.dataset)}>Preview</button>
                <button className="btn-secondary btn-sm" disabled={busy} onClick={() => archive(p)}>Archive eligible</button></div></td></tr>
          ))}</tbody></table></div>
      )}
      <h3 className="sd-section">History</h3>
      {runs.length === 0 ? <p className="cc-empty">No retention runs yet.</p> : (
        <div className="table-scroll"><table className="data-table">
          <thead><tr><th>Dataset</th><th className="num">Eligible</th><th className="num">Archived</th><th className="num">Purged</th><th>When</th></tr></thead>
          <tbody>{runs.map((r) => <tr key={r.id}><td>{r.dataset}</td><td className="num">{r.eligible_records}</td><td className="num">{r.archived_records}</td><td className="num">{r.purged_records}</td><td className="nowrap">{new Date(r.created_at).toLocaleString()}</td></tr>)}</tbody></table></div>
      )}
    </section>
  );
}
