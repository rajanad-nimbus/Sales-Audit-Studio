'use client';
import { useCallback, useEffect, useState } from 'react';
import { api, money } from '@/lib/api';
import { useToast } from '@/components/Feedback';
import { useMe } from '@/components/MeProvider';
import { Alert, Badge, Button, Card, DataTable, Field } from '@/components/ui';
import { apiError } from '@/lib/storeDays';

interface Tender {
  id: string;
  code: string;
  name: string;
  active: boolean;
  tender_group: string;
  requires_reference: boolean;
  max_amount: string | null;
  tender_lines: number;
}
interface Listing {
  known_codes: string[];
  groups: string[];
  items: Tender[];
}
const BLANK = {
  code: '',
  name: '',
  active: true,
  tender_group: 'Other',
  requires_reference: false,
  max_amount: '',
};

/** The forms of payment a retailer accepts, and the audit rules each one carries. */
export function TenderTypes() {
  const toast = useToast();
  const { me } = useMe();
  const canEdit = !!me && ['finance', 'it', 'admin'].includes(me.role);
  const [data, setData] = useState<Listing | null>(null);
  const [form, setForm] = useState(BLANK);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Listing>('/api/tender-types');
      setData(data);
    } catch (e) {
      setError(apiError(e, 'Could not load the tender types.'));
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const defined = new Set((data?.items ?? []).map((t) => t.code));
  const available = (data?.known_codes ?? []).filter((c) => !defined.has(c));

  const save = async () => {
    setBusy(true);
    setError(null);
    const body = { ...form, max_amount: form.max_amount.trim() || null };
    try {
      if (editing) await api.put(`/api/tender-types/${editing}`, body);
      else await api.post('/api/tender-types', body);
      toast(`Tender ${form.code} saved.`, 'success');
      setForm(BLANK);
      setEditing(null);
      await load();
    } catch (e) {
      setError(apiError(e, 'Could not save the tender type.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Tender types">
      <p>
        Once any tender type is defined, a payment in a tender that is not defined and active is an
        audit error. A tender can also require a reference (a card slip or voucher number) and have
        a limit for one payment; payments that break either raise an error.
      </p>
      {error && (
        <Alert tone="error" onDismiss={() => setError(null)}>
          {error}
        </Alert>
      )}
      <div className="mt-md">
        <DataTable
          rows={data ? data.items : null}
          rowKey={(t) => t.id}
          emptyTitle="No tender types defined"
          emptyHint="Every tender is accepted until you define the first one."
          columns={[
            {
              key: 'code',
              header: 'Tender',
              primary: true,
              render: (t) => `${t.code} · ${t.name}`,
            },
            { key: 'group', header: 'Group', render: (t) => t.tender_group },
            {
              key: 'ref',
              header: 'Reference',
              render: (t) => (t.requires_reference ? 'Required' : 'Optional'),
            },
            {
              key: 'max',
              header: 'Limit per payment',
              numeric: true,
              render: (t) => (t.max_amount ? money(t.max_amount) : 'None'),
            },
            { key: 'used', header: 'Payments', numeric: true, render: (t) => t.tender_lines },
            {
              key: 'state',
              header: 'Status',
              render: (t) => (
                <Badge size="sm" variant={t.active ? 'success' : 'secondary'}>
                  {t.active ? 'Active' : 'Inactive'}
                </Badge>
              ),
            },
            ...(canEdit
              ? [
                  {
                    key: 'edit',
                    header: '',
                    stopRowClick: true,
                    render: (t: Tender) => (
                      <Button
                        size="sm"
                        onClick={() => {
                          setEditing(t.id);
                          setForm({
                            code: t.code,
                            name: t.name,
                            active: t.active,
                            tender_group: t.tender_group,
                            requires_reference: t.requires_reference,
                            max_amount: t.max_amount ?? '',
                          });
                        }}
                      >
                        Edit
                      </Button>
                    ),
                  },
                ]
              : []),
          ]}
        />
      </div>
      {canEdit && (editing || available.length > 0) && (
        <div className="sd-section">
          <h3>{editing ? `Edit ${form.code}` : 'Add a tender type'}</h3>
          <div className="form-row">
            <Field label="Tender" hint="The codes the feed can send">
              <select
                value={form.code}
                disabled={!!editing}
                onChange={(e) =>
                  setForm({
                    ...form,
                    code: e.target.value,
                    name: form.name || e.target.value,
                  })
                }
              >
                <option value="">Choose…</option>
                {(editing ? [form.code] : available).map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </Field>
            <Field label="Name">
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </Field>
            <Field label="Group">
              <select
                value={form.tender_group}
                onChange={(e) => setForm({ ...form, tender_group: e.target.value })}
              >
                {(data?.groups ?? []).map((g) => (
                  <option key={g}>{g}</option>
                ))}
              </select>
            </Field>
            <Field label="Limit per payment" hint="Blank for no limit">
              <input
                type="number"
                min="0.01"
                step="0.01"
                value={form.max_amount}
                onChange={(e) => setForm({ ...form, max_amount: e.target.value })}
              />
            </Field>
          </div>
          <div className="cluster">
            <label>
              <input
                type="checkbox"
                checked={form.requires_reference}
                onChange={(e) => setForm({ ...form, requires_reference: e.target.checked })}
              />{' '}
              Requires a reference
            </label>
            <label>
              <input
                type="checkbox"
                checked={form.active}
                onChange={(e) => setForm({ ...form, active: e.target.checked })}
              />{' '}
              Active
            </label>
            <Button
              variant="primary"
              disabled={busy || !form.code || !form.name.trim()}
              onClick={() => void save()}
            >
              {editing ? 'Save' : 'Add tender type'}
            </Button>
            {editing && (
              <Button
                onClick={() => {
                  setEditing(null);
                  setForm(BLANK);
                }}
              >
                Cancel
              </Button>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
