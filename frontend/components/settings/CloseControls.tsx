'use client';
import { useCallback, useEffect, useState } from 'react';
import { api, money } from '@/lib/api';
import { useToast } from '@/components/Feedback';
import { useMe } from '@/components/MeProvider';
import { Alert, Badge, Button, Card, DataTable, Field } from '@/components/ui';
import { apiError } from '@/lib/storeDays';

interface ToleranceRule {
  id: string;
  total_name: string | null;
  location_trait: string | null;
  within_amount: string;
  case_amount: string;
  updated_by: string | null;
  updated_at: string;
}
interface Period {
  id: string;
  period_end: string;
  status: string;
  reason: string | null;
  closed_by: string;
  closed_at: string;
  reopened_by: string | null;
  reopened_reason: string | null;
}

// The reconciliation comparisons that block closure; configured totals are added when readable.
const COMPARISONS = ['POS to GL Variance', 'Processor to Bank Variance', 'POS Control Declared'];
const EMPTY = { total_name: '', location_trait: '', within_amount: '0', case_amount: '0' };

/** Variance tolerance rules and accounting period close, on one Configuration screen. */
export function CloseControls() {
  const { me } = useMe();
  const canEdit = !!me && (me.role === 'finance' || me.role === 'admin');
  return (
    <div className="stack">
      <Tolerances canEdit={canEdit} />
      <Periods canEdit={canEdit} />
    </div>
  );
}

function Tolerances({ canEdit }: { canEdit: boolean }) {
  const toast = useToast();
  const [rules, setRules] = useState<ToleranceRule[] | null>(null);
  const [totals, setTotals] = useState<string[]>(COMPARISONS);
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<ToleranceRule[]>('/api/variance-tolerances');
      setRules(data);
    } catch (e) {
      setError(apiError(e, 'Could not load the tolerance rules.'));
    }
  }, []);
  useEffect(() => {
    load();
    api
      .get<{ name: string }[]>('/api/total-definitions')
      .then(({ data }) => setTotals([...new Set([...COMPARISONS, ...data.map((d) => d.name)])]))
      .catch(() => {
        /* configured totals are a convenience; the comparisons are always listed */
      });
  }, [load]);

  const save = async () => {
    setBusy(true);
    setError(null);
    const body = {
      total_name: form.total_name.trim() || null,
      location_trait: form.location_trait.trim() || null,
      within_amount: form.within_amount,
      case_amount: form.case_amount,
    };
    try {
      if (editing) await api.put(`/api/variance-tolerances/${editing}`, body);
      else await api.post('/api/variance-tolerances', body);
      toast('Tolerance saved. Open store days use it straight away.', 'success');
      setForm(EMPTY);
      setEditing(null);
      await load();
    } catch (e) {
      setError(apiError(e, 'Could not save the tolerance.'));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (rule: ToleranceRule) => {
    setBusy(true);
    try {
      await api.delete(`/api/variance-tolerances/${rule.id}`);
      toast('Tolerance removed.', 'success');
      await load();
    } catch (e) {
      setError(apiError(e, 'Could not remove the tolerance.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Variance tolerances">
      <p>
        A variance up to the tolerance is accepted. Above it, the variance needs a reason; above the
        case threshold, it needs a linked case or correction. Without a rule, every cent needs a
        case. The most specific rule wins: a total at a store group, then a total everywhere, then
        every total at a store group, then every total.
      </p>
      {error && (
        <Alert tone="error" onDismiss={() => setError(null)}>
          {error}
        </Alert>
      )}
      <div className="mt-md">
        <DataTable
          rows={rules}
          rowKey={(r) => r.id}
          emptyTitle="No tolerance rules"
          emptyHint="Every variance needs a case until a rule is added."
          columns={[
            {
              key: 'total',
              header: 'Total',
              primary: true,
              render: (r) => r.total_name ?? 'Every total',
            },
            {
              key: 'group',
              header: 'Store group',
              render: (r) => r.location_trait ?? 'Every store',
            },
            {
              key: 'within',
              header: 'Within tolerance',
              numeric: true,
              render: (r) => `up to ${money(r.within_amount)}`,
            },
            {
              key: 'case',
              header: 'Needs a case',
              numeric: true,
              render: (r) => `above ${money(r.case_amount)}`,
            },
            { key: 'by', header: 'Updated by', render: (r) => r.updated_by ?? '—' },
            ...(canEdit
              ? [
                  {
                    key: 'edit',
                    header: '',
                    stopRowClick: true,
                    render: (r: ToleranceRule) => (
                      <span className="cluster">
                        <Button
                          size="sm"
                          onClick={() => {
                            setEditing(r.id);
                            setForm({
                              total_name: r.total_name ?? '',
                              location_trait: r.location_trait ?? '',
                              within_amount: r.within_amount,
                              case_amount: r.case_amount,
                            });
                          }}
                        >
                          Edit
                        </Button>
                        <Button size="sm" disabled={busy} onClick={() => void remove(r)}>
                          Remove
                        </Button>
                      </span>
                    ),
                  },
                ]
              : []),
          ]}
        />
      </div>
      {canEdit && (
        <div className="sd-section">
          <h3>{editing ? 'Edit tolerance' : 'Add a tolerance'}</h3>
          <div className="form-row">
            <Field label="Total" hint="Blank applies to every total">
              <input
                list="tolerance-totals"
                value={form.total_name}
                onChange={(e) => setForm({ ...form, total_name: e.target.value })}
                placeholder="Every total"
              />
              <datalist id="tolerance-totals">
                {totals.map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
            </Field>
            <Field label="Store group" hint="A store location trait; blank is every store">
              <input
                value={form.location_trait}
                onChange={(e) => setForm({ ...form, location_trait: e.target.value })}
                placeholder="Every store"
              />
            </Field>
            <Field label="Tolerance">
              <input
                type="number"
                min="0"
                step="0.01"
                value={form.within_amount}
                onChange={(e) => setForm({ ...form, within_amount: e.target.value })}
              />
            </Field>
            <Field label="Case threshold">
              <input
                type="number"
                min="0"
                step="0.01"
                value={form.case_amount}
                onChange={(e) => setForm({ ...form, case_amount: e.target.value })}
              />
            </Field>
          </div>
          <div className="cluster">
            <Button
              variant="primary"
              disabled={
                busy ||
                form.within_amount === '' ||
                form.case_amount === '' ||
                Number(form.case_amount) < Number(form.within_amount)
              }
              onClick={() => void save()}
            >
              {editing ? 'Save tolerance' : 'Add tolerance'}
            </Button>
            {editing && (
              <Button
                onClick={() => {
                  setEditing(null);
                  setForm(EMPTY);
                }}
              >
                Cancel
              </Button>
            )}
            {Number(form.case_amount) < Number(form.within_amount) && (
              <span className="hint">The case threshold cannot be below the tolerance.</span>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

function Periods({ canEdit }: { canEdit: boolean }) {
  const toast = useToast();
  const [periods, setPeriods] = useState<Period[] | null>(null);
  const [closedThrough, setClosedThrough] = useState<string | null>(null);
  const [periodEnd, setPeriodEnd] = useState('');
  const [reason, setReason] = useState('');
  const [reopenReason, setReopenReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const today = new Date().toISOString().slice(0, 10);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get('/api/periods');
      setPeriods(data.periods);
      setClosedThrough(data.closed_through);
    } catch (e) {
      setError(apiError(e, 'Could not load the period closes.'));
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const latest = periods?.find((p) => p.status === 'Closed' && p.period_end === closedThrough);

  const run = async (call: () => Promise<unknown>, done: string, after: () => void) => {
    setBusy(true);
    setError(null);
    try {
      await call();
      toast(done, 'success');
      after();
      await load();
    } catch (e) {
      setError(apiError(e, 'The period could not be changed.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Period close">
      <p>
        Closing the books through a date stops every store day on or before it from being reopened.
        Every store day in the period must be closed first. A store day whose exports the
        destination has acknowledged cannot be reopened either.
      </p>
      <p>
        <b>Closed through:</b>{' '}
        {closedThrough ? (
          <Badge variant="primary">{closedThrough}</Badge>
        ) : (
          <span className="hint">No period has been closed</span>
        )}
      </p>
      {error && (
        <Alert tone="error" onDismiss={() => setError(null)}>
          {error}
        </Alert>
      )}
      {canEdit && (
        <div className="form-row">
          <Field label="Close through">
            <input
              type="date"
              value={periodEnd}
              min={closedThrough ?? undefined}
              max={today}
              onChange={(e) => setPeriodEnd(e.target.value)}
            />
          </Field>
          <div className="grow">
            <Field label="Note">
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="For example: October month-end"
              />
            </Field>
          </div>
          <Button
            variant="primary"
            disabled={busy || !periodEnd}
            onClick={() =>
              void run(
                () => api.post('/api/periods', { period_end: periodEnd, reason }),
                `The books are closed through ${periodEnd}.`,
                () => {
                  setPeriodEnd('');
                  setReason('');
                }
              )
            }
          >
            Close period
          </Button>
        </div>
      )}
      {canEdit && latest && (
        <div className="form-row">
          <div className="grow">
            <Field label={`Reopen the period through ${latest.period_end}`}>
              <input
                value={reopenReason}
                onChange={(e) => setReopenReason(e.target.value)}
                placeholder="Why it must reopen"
              />
            </Field>
          </div>
          <Button
            disabled={busy || !reopenReason.trim()}
            onClick={() =>
              void run(
                () => api.post(`/api/periods/${latest.id}/reopen`, { rationale: reopenReason }),
                `The period through ${latest.period_end} is reopened.`,
                () => setReopenReason('')
              )
            }
          >
            Reopen period
          </Button>
        </div>
      )}
      <div className="mt-md">
        <DataTable
          rows={periods}
          rowKey={(p) => p.id}
          emptyTitle="No period closes yet"
          columns={[
            { key: 'end', header: 'Through', primary: true, render: (p) => p.period_end },
            {
              key: 'status',
              header: 'Status',
              render: (p) => (
                <Badge size="sm" variant={p.status === 'Closed' ? 'success' : 'secondary'}>
                  {p.status}
                </Badge>
              ),
            },
            {
              key: 'closed',
              header: 'Closed',
              render: (p) => `${p.closed_by} · ${new Date(p.closed_at).toLocaleDateString()}`,
            },
            {
              key: 'note',
              header: 'Note',
              render: (p) =>
                p.reopened_by
                  ? `Reopened by ${p.reopened_by}: ${p.reopened_reason}`
                  : (p.reason ?? '—'),
            },
          ]}
        />
      </div>
    </Card>
  );
}
