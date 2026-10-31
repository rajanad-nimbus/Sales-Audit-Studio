'use client';

import { useCallback, useEffect, useState } from 'react';
import { api, ExportDest } from '@/lib/api';
import { SkeletonRows, useToast } from '@/components/Feedback';
import { useMe } from '@/components/MeProvider';
import { Alert, Badge, Button, Card, DataTable, Field } from '@/components/ui';
import { SettingsSection } from '@/components/settings/SettingsSection';

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
    <SettingsSection
      title="Export destinations"
      description="Where approved exports are delivered. IT adds and enables destinations; Finance and auditors can see them."
      actions={
        canEdit && (
          <Button variant={adding ? 'secondary' : 'primary'} onClick={() => setAdding(!adding)}>
            {adding ? 'Cancel' : 'Add destination'}
          </Button>
        )
      }
    >
      {adding && (
        <Card title="New destination">
          <div className="cfg-form">
            <Field label="Name">
              <input
                placeholder="For example ERP/GL production"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </Field>
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
                <Field label="URL">
                  <input
                    placeholder="https://erp.example.com/nimbus/inbound"
                    value={form.url}
                    onChange={(e) => setForm({ ...form, url: e.target.value })}
                    aria-invalid={!!form.url && !/^https?:\/\//.test(form.url)}
                  />
                </Field>
                <Field label="Signing secret">
                  <input
                    type="password"
                    value={form.secret}
                    onChange={(e) => setForm({ ...form, secret: e.target.value })}
                  />
                </Field>
              </>
            )}
          </div>
          <p className="hint">
            Webhook deliveries carry an HMAC-SHA256 signature in <code>X-Nimbus-Signature</code> and
            an <code>X-Nimbus-Idempotency-Key</code> so the receiver can reject forgeries and
            replays.
          </p>
          <div>
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
        </Card>
      )}
      <Card title={`Destinations (${dests.length})`}>
        <DataTable
          columns={[
            { key: 'name', header: 'Name', primary: true, render: (d) => d.name },
            {
              key: 'kind',
              header: 'Kind',
              render: (d) => (d.kind === 'webhook' ? 'Webhook' : 'File drop'),
            },
            { key: 'format', header: 'Format', render: (d) => d.format.toUpperCase() },
            {
              key: 'target',
              header: 'Target',
              render: (d) => d.config.url ?? 'Exports folder',
            },
            {
              key: 'status',
              header: 'Status',
              render: (d) => (
                <Badge variant={d.enabled ? 'success' : 'secondary'} size="sm">
                  {d.enabled ? 'Enabled' : 'Disabled'}
                </Badge>
              ),
            },
            {
              key: 'actions',
              header: '',
              render: (d) =>
                canEdit && (
                  <Button
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      act(async () => {
                        await api.post(`/api/exports/destinations/${d.id}/toggle`);
                        toast(
                          d.enabled ? 'Destination disabled.' : 'Destination enabled.',
                          'success'
                        );
                      })
                    }
                  >
                    {d.enabled ? 'Disable' : 'Enable'}
                  </Button>
                ),
            },
          ]}
          rows={dests}
          rowKey={(d) => String(d.id)}
          emptyTitle="No destinations yet"
          emptyHint="Add a webhook or file drop to start exporting."
        />
      </Card>
    </SettingsSection>
  );
}
