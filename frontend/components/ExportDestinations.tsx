'use client';

import { useCallback, useEffect, useState } from 'react';
import { api, ExportDest } from '@/lib/api';
import { SkeletonRows, useToast } from '@/components/Feedback';
import { useMe } from '@/components/MeProvider';
import { Alert, Button, EmptyState, Field } from '@/components/ui';

const apiError = (e: unknown, fallback: string) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;

/** Where exports are delivered. IT adds and enables destinations; Finance and auditors can see them. */
export function ExportDestinations() {
  const toast = useToast();
  const { me } = useMe();
  const canEdit = !!me && (me.role === 'it' || me.role === 'admin');
  const [dests, setDests] = useState<ExportDest[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    name: '',
    kind: 'webhook',
    format: 'json',
    url: '',
    secret: '',
  });

  const load = useCallback(async () => {
    try {
      setDests((await api.get('/api/exports/destinations')).data);
      setError(null);
    } catch (e) {
      setError(apiError(e, 'Could not load destinations.'));
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
      await load();
    } catch (e) {
      toast(apiError(e, 'That did not work.'), 'error');
    } finally {
      setBusy(false);
    }
  };
  const addDest = () =>
    act(async () => {
      await api.post('/api/exports/destinations', form);
      setAdding(false);
      setForm({ name: '', kind: 'webhook', format: 'json', url: '', secret: '' });
      toast('Destination created.', 'success');
    });

  if (error)
    return (
      <Alert
        tone="error"
        action={
          <Button size="sm" className="alert-close" onClick={load}>
            Retry
          </Button>
        }
      >
        {error}
      </Alert>
    );
  if (!dests) return <SkeletonRows rows={4} label="Loading destinations" />;
  return (
    <section id="destinations">
      <div className="page-header" style={{ marginBottom: '.5rem' }}>
        <h2 className="act-day" style={{ margin: 0 }}>
          Destinations <span className="cc-count">{dests.length}</span>
        </h2>
        {canEdit && (
          <Button size="sm" onClick={() => setAdding(!adding)}>
            {adding ? 'Cancel' : 'Add destination'}
          </Button>
        )}
      </div>
      {adding && (
        <div className="cc-card" style={{ marginBottom: 'var(--space-sm)' }}>
          <h3>New destination</h3>
          <div className="form-row">
            <label className="field grow">
              Name
              <input
                placeholder="For example ERP/GL production"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </label>
            <Field label="Kind">
              <select
                value={form.kind}
                onChange={(e) => setForm({ ...form, kind: e.target.value })}
              >
                <option value="webhook">Webhook (HTTP POST)</option>
                <option value="file">File drop</option>
              </select>
            </Field>
            <Field label="Format">
              <select
                value={form.format}
                onChange={(e) => setForm({ ...form, format: e.target.value })}
              >
                <option value="json">JSON</option>
                <option value="csv">CSV</option>
              </select>
            </Field>
            {form.kind === 'webhook' && (
              <>
                <label className="field grow">
                  URL
                  <input
                    placeholder="https://erp.example.com/nimbus/inbound"
                    value={form.url}
                    onChange={(e) => setForm({ ...form, url: e.target.value })}
                    aria-invalid={!!form.url && !/^https?:\/\//.test(form.url)}
                  />
                </label>
                <Field label="Signing secret">
                  <input
                    type="password"
                    value={form.secret}
                    onChange={(e) => setForm({ ...form, secret: e.target.value })}
                  />
                </Field>
              </>
            )}
            <Button
              variant="primary"

              disabled={
                busy ||
                !form.name.trim() ||
                (form.kind === 'webhook' && !/^https?:\/\//.test(form.url))
              }
              onClick={addDest}
            >
              Create
            </Button>
          </div>
          <p className="hint" style={{ marginTop: '.6rem' }}>
            Webhook deliveries carry an HMAC-SHA256 signature in <code>X-Nimbus-Signature</code> and
            an <code>X-Nimbus-Idempotency-Key</code> so the receiver can reject forgeries and
            replays.
          </p>
        </div>
      )}
      {dests.length === 0 ? (
        <EmptyState title="No destinations yet">
          <p>Add a webhook or file drop to start exporting.</p>
        </EmptyState>
      ) : (
        <div
          className="ac-tiles"
          style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))' }}
        >
          {dests.map((d) => (
            <div
              key={d.id}
              className="ac-tile"
              style={{ cursor: 'default', opacity: d.enabled ? 1 : 0.65 }}
            >
              <small>
                {d.kind === 'webhook' ? 'Webhook' : 'File drop'} · {d.format.toUpperCase()} ·{' '}
                <span
                  className={`badge badge-sm ${d.enabled ? 'badge-success' : 'badge-secondary'}`}
                >
                  {d.enabled ? 'Enabled' : 'Disabled'}
                </span>
              </small>
              <b style={{ fontSize: '1rem' }}>{d.name}</b>
              <em>{d.config.url ?? 'Writes files to the exports folder'}</em>
              <Button
                size="sm"

                style={{ marginTop: '.4rem' }}
                onClick={() =>
                  act(async () => {
                    await api.post(`/api/exports/destinations/${d.id}/toggle`);
                    toast(d.enabled ? 'Destination disabled.' : 'Destination enabled.', 'success');
                  })
                }
              >
                {d.enabled ? 'Disable' : 'Enable'}
              </Button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
