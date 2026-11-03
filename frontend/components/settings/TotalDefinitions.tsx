'use client';
import { useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { useToast } from '@/components/Feedback';
import { useMe } from '@/components/MeProvider';
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  Drawer,
  Field,
  MetricGrid,
  MetricTile,
} from '@/components/ui';

/** One stored revision, as returned by GET /api/total-definitions. */
export type TotalRevision = {
  id: string;
  name: string;
  revision: number;
  status: string;
  description: string | null;
  aggregation: string;
  measure: string;
  source_system: string | null;
  transaction_types: string[];
  tender_type: string | null;
  level: string;
  active: boolean;
  start_date: string | null;
  end_date: string | null;
  calculation_basis: string;
  combined_total_ids: string[];
  combined_operator: string | null;
  updated_by: string | null;
  submitted_by?: string | null;
  submitted_at?: string | null;
  submitted_impact_fingerprint?: string | null;
  approved_by?: string | null;
  [field: string]: unknown;
};

type ImpactRow = {
  store_id: string;
  business_date: string;
  store_day_status: string;
  current: string | null;
  proposed: string | null;
  change: string;
  changed_totals: { name: string; current: string | null; proposed: string | null }[];
  current_error: string | null;
  proposed_error: string | null;
  rule_changes: {
    rule_id: string;
    rule: string;
    total: string;
    threshold: string;
    current: string | null;
    proposed: string | null;
    effect: 'Newly breaches' | 'Clears';
  }[];
  would_go_stale: boolean;
  closure_requests_superseded: number;
};

type Impact = {
  definition_id: string;
  revision: number;
  replaces_revision: number | null;
  date_range: { start: string | null; end: string | null };
  limit: number;
  truncated: boolean;
  fingerprint: string;
  summary: {
    store_days: number;
    changed: number;
    net_change: string;
    absolute_change: string;
    closed: number;
    would_go_stale: number;
    closure_requests_superseded: number;
    errors: number;
    rules_newly_breached: number;
    rules_cleared: number;
  };
  rows: ImpactRow[];
};

/** Every revision of one total: the one in use, and a newer draft (or submission) if there is one. */
type TotalGroup = { name: string; live: TotalRevision | null; draft: TotalRevision | null };

const DRAFT = 'Worksheet';
const PENDING = 'Pending Approval';
const notLive = (row: TotalRevision) => row.status === DRAFT || row.status === PENDING;
const LEVELS = ['Store', 'Register', 'Cashier', 'Tender'];
// Fields the server owns; everything else is carried into the next revision unchanged.
const SERVER_FIELDS = [
  'id',
  'revision',
  'status',
  'updated_by',
  'updated_at',
  'submitted_by',
  'submitted_at',
  'submitted_impact_fingerprint',
  'approved_by',
  'approved_at',
];

const number = (v: string | number | null) =>
  v === null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
const signed = (v: string | number) => (Number(v) > 0 ? `+${number(v)}` : number(v));
const detail = (e: any, fallback: string) => e?.response?.data?.detail ?? fallback;

function groupRevisions(revisions: TotalRevision[]): TotalGroup[] {
  const byName = new Map<string, TotalRevision[]>();
  for (const row of revisions) byName.set(row.name, [...(byName.get(row.name) ?? []), row]);
  return [...byName.entries()]
    .map(([name, rows]) => {
      const sorted = [...rows].sort((a, b) => b.revision - a.revision);
      const live = sorted.find((row) => !notLive(row)) ?? null;
      const latest = sorted[0];
      const draft = notLive(latest) ? latest : null;
      return { name, live, draft };
    })
    .sort((a, b) => a.name.localeCompare(b.name));
}

function calculation(row: TotalRevision) {
  return row.calculation_basis === 'Combined'
    ? `Combined (${row.combined_operator || '?'})`
    : `${row.aggregation} of ${row.measure.toLowerCase()}`;
}

function filter(row: TotalRevision) {
  return [...row.transaction_types, row.tender_type].filter(Boolean).join(', ') || 'All records';
}

function dates(row: TotalRevision) {
  if (!row.start_date && !row.end_date) return 'Always';
  return `${row.start_date ?? 'Start'} to ${row.end_date ?? 'open-ended'}`;
}

export function TotalDefinitions({
  revisions,
  onChanged,
}: {
  revisions: TotalRevision[];
  onChanged: () => Promise<void>;
}) {
  const groups = useMemo(() => groupRevisions(revisions), [revisions]);
  const [openName, setOpenName] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const open = groups.find((group) => group.name === openName) ?? null;
  const drafts = groups.filter((group) => group.draft?.status === DRAFT).length;
  const awaiting = groups.filter((group) => group.draft?.status === PENDING).length;

  return (
    <Card title="Audit total definitions">
      <p>
        Live revisions are calculated during Store Day re-totaling. A change is saved as a draft and
        only goes live after its impact on store days is previewed, submitted, and approved by a
        second Finance user. Rules reference <b>Configured: [definition name]</b>.
      </p>
      <div className="cluster mt-sm">
        <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
          New total
        </Button>
        {drafts > 0 && (
          <span className="page-sub">
            {drafts} draft{drafts === 1 ? '' : 's'} waiting to be previewed and submitted.
          </span>
        )}
        {awaiting > 0 && <span className="page-sub">{awaiting} awaiting a second approver.</span>}
      </div>
      <div className="mt-md">
        <DataTable
          rows={groups}
          rowKey={(group) => group.name}
          onRowClick={(group) => setOpenName(group.name)}
          emptyTitle="No totals defined."
          columns={[
            { key: 'name', header: 'Name', primary: true, render: (g) => g.name },
            {
              key: 'calculation',
              header: 'Calculation',
              render: (g) => calculation((g.draft ?? g.live)!),
            },
            {
              key: 'source',
              header: 'Source',
              render: (g) => (g.draft ?? g.live)!.source_system || 'Any',
            },
            { key: 'level', header: 'Level', render: (g) => (g.draft ?? g.live)!.level },
            {
              key: 'state',
              header: 'Revision',
              nowrap: true,
              render: (g) => (
                <span className="cluster">
                  {g.live ? (
                    <Badge size="sm" variant={g.live.active ? 'success' : 'secondary'}>
                      {g.live.active ? 'Live' : 'Retired'} r{g.live.revision}
                    </Badge>
                  ) : (
                    <Badge size="sm" variant="secondary">
                      Not live
                    </Badge>
                  )}
                  {g.draft && (
                    <Badge size="sm" variant="warning">
                      {g.draft.status === PENDING ? 'Awaiting approval' : 'Draft'} r
                      {g.draft.revision}
                    </Badge>
                  )}
                </span>
              ),
            },
          ]}
        />
      </div>
      {open && (
        <TotalDrawer
          key={open.name}
          group={open}
          onClose={() => setOpenName(null)}
          onChanged={onChanged}
        />
      )}
      {creating && (
        <Drawer title="New total" onClose={() => setCreating(false)}>
          <DraftForm
            base={null}
            onCancel={() => setCreating(false)}
            onSaved={async (saved) => {
              await onChanged();
              setCreating(false);
              setOpenName(saved.name);
            }}
          />
        </Drawer>
      )}
    </Card>
  );
}

function TotalDrawer({
  group,
  onClose,
  onChanged,
}: {
  group: TotalGroup;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const toast = useToast();
  const { me } = useMe();
  const [editing, setEditing] = useState(false);
  const [impact, setImpact] = useState<Impact | null>(null);
  const [busy, setBusy] = useState<'preview' | 'submit' | 'activate' | 'return' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [returnReason, setReturnReason] = useState('');
  const draft = group.draft;
  const pending = draft?.status === PENDING;
  // The approver must be someone other than the editor and the submitter.
  const ownSubmission =
    pending && !!me?.user && (me.user === draft?.submitted_by || me.user === draft?.updated_by);
  // A preview only authorises the draft it was run for.
  const preview = impact && draft && impact.definition_id === draft.id ? impact : null;
  const moved = pending && !!preview && preview.fingerprint !== draft?.submitted_impact_fingerprint;

  const runPreview = async () => {
    if (!draft) return;
    setBusy('preview');
    setError(null);
    try {
      const { data } = await api.post<Impact>(`/api/total-definitions/${draft.id}/impact`);
      setImpact(data);
    } catch (e) {
      setError(detail(e, 'Could not preview the impact.'));
    } finally {
      setBusy(null);
    }
  };

  const decide = async (
    step: 'submit' | 'activate',
    done: string,
    failed: string
  ): Promise<void> => {
    if (!draft || !preview) return;
    setBusy(step);
    setError(null);
    try {
      await api.post(`/api/total-definitions/${draft.id}/${step}`, {
        impact_fingerprint: preview.fingerprint,
      });
      setImpact(null);
      await onChanged();
      toast(done, 'success');
    } catch (e: any) {
      // A changed impact makes the reviewed preview worthless; make the user look again.
      if (e?.response?.status === 409) setImpact(null);
      setError(detail(e, failed));
    } finally {
      setBusy(null);
    }
  };

  const sendBack = async () => {
    if (!draft) return;
    setBusy('return');
    setError(null);
    try {
      await api.post(`/api/total-definitions/${draft.id}/return`, { rationale: returnReason });
      setImpact(null);
      setReturnReason('');
      await onChanged();
      toast(`${group.name} revision ${draft.revision} was returned to draft.`, 'success');
    } catch (e) {
      setError(detail(e, 'Could not return the submission.'));
    } finally {
      setBusy(null);
    }
  };

  if (editing) {
    return (
      <Drawer
        title={draft ? `Edit draft of ${group.name}` : `New draft of ${group.name}`}
        onClose={onClose}
      >
        <DraftForm
          base={draft ?? group.live}
          onCancel={() => setEditing(false)}
          onSaved={async () => {
            setImpact(null);
            await onChanged();
            setEditing(false);
          }}
        />
      </Drawer>
    );
  }

  return (
    <Drawer
      title={group.name}
      onClose={onClose}
      footer={
        <div className="cluster">
          <Button size="sm" onClick={() => setEditing(true)}>
            {pending ? 'Revise' : draft ? 'Edit draft' : 'Start a draft'}
          </Button>
          {draft && !ownSubmission && (
            <>
              <Button size="sm" disabled={busy !== null} onClick={() => void runPreview()}>
                {busy === 'preview' ? 'Previewing…' : preview ? 'Preview again' : 'Preview impact'}
              </Button>
              {pending ? (
                <Button
                  size="sm"
                  variant="primary"
                  disabled={!preview || moved || busy !== null || preview.summary.errors > 0}
                  onClick={() =>
                    void decide(
                      'activate',
                      `${group.name} revision ${draft.revision} is live.`,
                      'Could not approve the submission.'
                    )
                  }
                >
                  {busy === 'activate' ? 'Approving…' : `Approve & activate r${draft.revision}`}
                </Button>
              ) : (
                <Button
                  size="sm"
                  variant="primary"
                  disabled={!preview || busy !== null || preview.summary.errors > 0}
                  onClick={() =>
                    void decide(
                      'submit',
                      `${group.name} revision ${draft.revision} is waiting for a second approver.`,
                      'Could not submit the draft.'
                    )
                  }
                >
                  {busy === 'submit' ? 'Submitting…' : `Submit r${draft.revision} for approval`}
                </Button>
              )}
            </>
          )}
        </div>
      }
    >
      <div className="stack">
        {error && (
          <Alert tone="error" onDismiss={() => setError(null)}>
            {error}
          </Alert>
        )}
        {draft && (
          <RevisionSummary
            title={`${pending ? 'Submitted' : 'Draft'} r${draft.revision}`}
            row={draft}
            compare={group.live}
          />
        )}
        <RevisionSummary
          title={group.live ? `Live r${group.live.revision}` : 'Not live yet'}
          row={group.live}
        />
        {pending && (
          <Alert tone={ownSubmission ? 'info' : 'warning'}>
            Submitted by {draft?.submitted_by ?? 'someone'}
            {draft?.submitted_at
              ? ` on ${new Date(draft.submitted_at).toLocaleString()}`
              : ''}.{' '}
            {ownSubmission
              ? 'Another Finance user must review the impact and approve it.'
              : 'Preview the impact; you can approve it only if it is unchanged since submission.'}
          </Alert>
        )}
        {moved && (
          <Alert tone="error">
            The impact has changed since this was submitted. Return it so the editor can review the
            new impact and submit again.
          </Alert>
        )}
        {pending && !ownSubmission && (
          <div className="form-row">
            <div className="grow">
              <Field label="Return to draft">
                <input
                  value={returnReason}
                  onChange={(e) => setReturnReason(e.target.value)}
                  placeholder="Why it is not ready"
                />
              </Field>
            </div>
            <Button
              size="sm"
              disabled={busy !== null || !returnReason.trim()}
              onClick={() => void sendBack()}
            >
              {busy === 'return' ? 'Returning…' : 'Return'}
            </Button>
          </div>
        )}
        {draft && !preview && !ownSubmission && (
          <p className="hint">
            Preview the impact to see every store day this {pending ? 'submission' : 'draft'} would
            change. {pending ? 'Approval' : 'Submission'} stays disabled until you have.
          </p>
        )}
        {preview && <ImpactReport impact={preview} />}
      </div>
    </Drawer>
  );
}

function RevisionSummary({
  title,
  row,
  compare,
}: {
  title: string;
  row: TotalRevision | null;
  compare?: TotalRevision | null;
}) {
  if (!row) {
    return (
      <section>
        <h3>{title}</h3>
        <p className="hint">This total has never been activated, so no store day uses it.</p>
      </section>
    );
  }
  const items: [string, (r: TotalRevision) => string][] = [
    ['Calculation', calculation],
    ['Source', (r) => r.source_system || 'Any'],
    ['Records', filter],
    ['Level', (r) => r.level],
    ['Effective', dates],
    ['In use', (r) => (r.active ? 'Yes' : 'No, retired')],
  ];
  return (
    <section>
      <h3>{title}</h3>
      {row.description && <p>{row.description}</p>}
      <dl className="kv">
        {items.map(([label, value]) => {
          const changed = compare && value(compare) !== value(row);
          return (
            <div key={label} style={{ display: 'contents' }}>
              <dt>{label}</dt>
              <dd>
                {value(row)}
                {changed && (
                  <>
                    {' '}
                    <Badge size="sm" variant="info">
                      was {value(compare)}
                    </Badge>
                  </>
                )}
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}

function ImpactReport({ impact }: { impact: Impact }) {
  const [showAll, setShowAll] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const { summary } = impact;
  const key = (row: ImpactRow) => `${row.store_id}|${row.business_date}`;
  const hasDetail = (row: ImpactRow) =>
    Boolean(row.proposed_error || row.changed_totals.length || row.rule_changes.length);
  const changed = (row: ImpactRow) =>
    Number(row.change) !== 0 ||
    row.changed_totals.length > 0 ||
    row.rule_changes.length > 0 ||
    row.proposed_error !== row.current_error;
  const rows = impact.rows
    .filter((row) => showAll || changed(row))
    .sort((a, b) => Math.abs(Number(b.change)) - Math.abs(Number(a.change)));
  const range = impact.date_range;

  return (
    <section className="stack stack-sm">
      <h3>Impact of activating r{impact.revision}</h3>
      <p className="page-sub">
        Store days from {range.start ?? 'the first'} to {range.end ?? 'the latest'}
        {impact.replaces_revision ? `, compared with live r${impact.replaces_revision}` : ''}. Both
        sides are recalculated from the same transactions.
      </p>
      {impact.truncated && (
        <Alert tone="warning">
          Only the first {impact.limit} store days are shown. The change reaches further than this
          preview.
        </Alert>
      )}
      {summary.errors > 0 && (
        <Alert tone="error">
          {summary.errors} store day{summary.errors === 1 ? '' : 's'} would fail to calculate. Fix
          the draft before activating it.
        </Alert>
      )}
      <MetricGrid>
        <MetricTile
          label="Store days changed"
          value={`${summary.changed} of ${summary.store_days}`}
        />
        <MetricTile label="Net change" value={signed(summary.net_change)} />
        <MetricTile label="Closed days hit" value={summary.closed} />
        <MetricTile label="Days to re-total" value={summary.would_go_stale} />
        <MetricTile label="Closure requests voided" value={summary.closure_requests_superseded} />
        <MetricTile label="Rules newly breached" value={summary.rules_newly_breached} />
        <MetricTile label="Rules cleared" value={summary.rules_cleared} />
      </MetricGrid>
      {summary.rules_newly_breached > 0 && (
        <Alert tone="warning">
          After the next re-total, {summary.rules_newly_breached} audit rule result
          {summary.rules_newly_breached === 1 ? '' : 's'} would newly fail and raise a case.
        </Alert>
      )}
      <DataTable
        rows={rows}
        rowKey={key}
        onRowClick={(row) =>
          hasDetail(row) && setExpanded((open) => (open === key(row) ? null : key(row)))
        }
        expandedKey={expanded}
        emptyTitle={showAll ? 'No store days in range.' : 'No store day changes.'}
        emptyHint={showAll ? undefined : 'Activating this draft leaves every value as it is.'}
        columns={[
          {
            key: 'day',
            header: 'Store day',
            primary: true,
            render: (row) => (
              <>
                {row.store_id} · {row.business_date}
                {row.store_day_status === 'Closed' && (
                  <>
                    {' '}
                    <Badge size="sm" variant="secondary">
                      Closed
                    </Badge>
                  </>
                )}
              </>
            ),
          },
          {
            key: 'value',
            header: 'Live → draft',
            numeric: true,
            nowrap: true,
            render: (row) =>
              row.proposed_error ? (
                <span title={row.proposed_error}>
                  <Badge size="sm" variant="error">
                    Fails
                  </Badge>
                </span>
              ) : (
                `${number(row.current)} → ${number(row.proposed)}`
              ),
          },
          {
            key: 'change',
            header: 'Change',
            numeric: true,
            render: (row) => signed(row.change),
          },
          {
            key: 'rules',
            header: 'Audit rules',
            render: (row) =>
              row.rule_changes.length === 0 ? (
                '—'
              ) : (
                <span className="cluster">
                  {row.rule_changes.map((r) => (
                    <Badge
                      key={r.rule_id}
                      size="sm"
                      variant={r.effect === 'Newly breaches' ? 'error' : 'success'}
                    >
                      {r.effect === 'Newly breaches' ? 'Fails' : 'Clears'}: {r.rule}
                    </Badge>
                  ))}
                </span>
              ),
          },
        ]}
        renderDetail={(row) =>
          hasDetail(row) ? (
            <div className="hint">
              {row.proposed_error && <p>{row.proposed_error}</p>}
              {row.changed_totals.map((total) => (
                <p key={total.name}>
                  Also changes {total.name}: {number(total.current)} → {number(total.proposed)}
                </p>
              ))}
              {row.rule_changes.map((r) => (
                <p key={r.rule_id}>
                  {r.rule} {r.effect.toLowerCase()}: {r.total} {number(r.current)} →{' '}
                  {number(r.proposed)} against a threshold of {number(r.threshold)}
                </p>
              ))}
            </div>
          ) : null
        }
      />
      <div>
        <Button size="sm" variant="ghost" onClick={() => setShowAll((value) => !value)}>
          {showAll ? 'Show changed store days only' : 'Show unchanged store days too'}
        </Button>
      </div>
    </section>
  );
}

/** Edit form for the next draft revision; fields it does not show are carried over as they are. */
function DraftForm({
  base,
  onCancel,
  onSaved,
}: {
  base: TotalRevision | null;
  onCancel: () => void;
  onSaved: (saved: TotalRevision) => Promise<void>;
}) {
  const [form, setForm] = useState({
    name: base?.name ?? '',
    description: base?.description ?? '',
    aggregation: base?.aggregation ?? 'Sum',
    measure: base?.measure ?? 'Amount',
    source_system: base?.source_system ?? '',
    transaction_types: (base?.transaction_types ?? []).join(', '),
    tender_type: base?.tender_type ?? '',
    level: base?.level ?? 'Store',
    start_date: base?.start_date ?? '',
    end_date: base?.end_date ?? '',
    active: base?.active ?? true,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const combined = base?.calculation_basis === 'Combined';
  const set = (field: keyof typeof form) => (e: React.ChangeEvent<any>) =>
    setForm({ ...form, [field]: e.target.type === 'checkbox' ? e.target.checked : e.target.value });

  const save = async () => {
    setSaving(true);
    setError(null);
    const carried = Object.fromEntries(
      Object.entries(base ?? {}).filter(([key]) => !SERVER_FIELDS.includes(key))
    );
    const body = {
      ...carried,
      ...form,
      name: form.name.trim(),
      description: form.description.trim() || null,
      source_system: form.source_system.trim() || null,
      tender_type: form.tender_type.trim() || null,
      transaction_types: form.transaction_types
        .split(',')
        .map((type) => type.trim())
        .filter(Boolean),
      start_date: form.start_date || null,
      end_date: form.end_date || null,
      status: DRAFT,
    };
    try {
      const { data } = base
        ? await api.put<TotalRevision>(`/api/total-definitions/${base.id}`, body)
        : await api.post<TotalRevision>('/api/total-definitions', body);
      await onSaved(data);
    } catch (e) {
      setError(detail(e, 'Could not save the draft.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="stack">
      {error && (
        <Alert tone="error" onDismiss={() => setError(null)}>
          {error}
        </Alert>
      )}
      <p className="hint">
        Saving creates a draft revision. Nothing changes on any store day until the draft is
        previewed, submitted, and approved by a second Finance user.
      </p>
      {!base && (
        <Field label="Name">
          <input value={form.name} onChange={set('name')} />
        </Field>
      )}
      <Field label="Description">
        <textarea rows={2} value={form.description} onChange={set('description')} />
      </Field>
      {combined ? (
        <p className="hint">
          This is a combined total. Its operands and operator are kept as they are.
        </p>
      ) : (
        <>
          <div className="form-row">
            <Field label="Aggregation">
              <select value={form.aggregation} onChange={set('aggregation')}>
                <option>Sum</option>
                <option>Count</option>
              </select>
            </Field>
            <Field label="Measure">
              <select value={form.measure} onChange={set('measure')}>
                <option>Amount</option>
                <option>Tax</option>
              </select>
            </Field>
          </div>
          <Field label="Source system" hint="Leave empty for any source.">
            <input value={form.source_system} onChange={set('source_system')} />
          </Field>
          <Field label="Transaction types" hint="Comma-separated, for example Sale, Return.">
            <input value={form.transaction_types} onChange={set('transaction_types')} />
          </Field>
          <Field label="Tender type" hint="Leave empty for every tender.">
            <input value={form.tender_type} onChange={set('tender_type')} />
          </Field>
        </>
      )}
      <Field label="Balancing level">
        <select value={form.level} onChange={set('level')}>
          {LEVELS.map((level) => (
            <option key={level}>{level}</option>
          ))}
        </select>
      </Field>
      <div className="form-row">
        <Field label="Effective from">
          <input type="date" value={form.start_date} onChange={set('start_date')} />
        </Field>
        <Field label="Effective to">
          <input type="date" value={form.end_date} onChange={set('end_date')} />
        </Field>
      </div>
      <label className="cluster">
        <input type="checkbox" checked={form.active} onChange={set('active')} />
        In use. Clear this to retire the total when the draft is activated.
      </label>
      <div className="cluster">
        <Button
          size="sm"
          variant="primary"
          disabled={saving || !form.name.trim()}
          onClick={() => void save()}
        >
          {saving ? 'Saving…' : 'Save draft'}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
