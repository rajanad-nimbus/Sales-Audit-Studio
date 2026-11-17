'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, money } from '@/lib/api';
import { apiError } from '@/lib/storeDays';
import { downloadBlob } from '@/lib/csv';
import { StoreSelect } from '@/components/StoreSelect';
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
import type { Column } from '@/components/ui';
import { formatDateTime, formatNumber } from '@/lib/preferences';

/** One stored revision, as returned by GET /api/total-definitions. */
export type TotalRevision = {
  id: string;
  name: string;
  revision: number;
  status: string;
  description: string | null;
  aggregation: string;
  measure: string;
  parameters?: { realm: RestrictionRealm; field: string }[];
  rollups?: { realm: RestrictionRealm; field: string }[];
  realms?: RestrictionRealm[];
  joins?: { parent_realm: 'Transaction'; child_realm: Exclude<RestrictionRealm, 'Transaction'> }[];
  restrictions?: Restriction[];
  usages?: { destination_id: string }[];
  report_usages?: ('flash_totals' | 'flash_sales_ytd')[];
  source_system: string | null;
  transaction_types: string[];
  tender_type: string | null;
  level: string;
  active: boolean;
  start_date: string | null;
  end_date: string | null;
  calculation_basis: string;
  combined_total_ids: string[];
  combined_total_operators?: string[];
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

type CalculationPreview = {
  definition: TotalRevision;
  store_id: string;
  business_date: string;
  value: number | string;
  currency: string | null;
  record_count: number | null;
  dimensions: { dimension: string | null; rollup_key?: string | null; value: number | string }[];
};
type ExportDestination = { id: string; name: string; enabled: boolean };

/** Every revision of one total: the one in use, and a newer draft (or submission) if there is one. */
type TotalGroup = { name: string; live: TotalRevision | null; draft: TotalRevision | null };

const DRAFT = 'Worksheet';
const PENDING = 'Pending Approval';
const notLive = (row: TotalRevision) => row.status === DRAFT || row.status === PENDING;
const LEVELS = ['Store', 'Register', 'Cashier', 'Tender'];
type RestrictionRealm = 'Transaction' | 'Line' | 'Tax' | 'Tender' | 'Discount';
type Restriction = {
  realm: RestrictionRealm;
  field: string;
  operator: 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte' | 'contains' | 'is_empty' | 'not_empty';
  value: string;
};
type TotalRealmJoin = {
  parent_realm: 'Transaction';
  child_realm: Exclude<RestrictionRealm, 'Transaction'>;
};
const restrictionFields: Record<
  RestrictionRealm,
  { key: string; label: string; numeric?: boolean }[]
> = {
  Transaction: [
    { key: 'transaction_type', label: 'Record type' },
    { key: 'transaction_subtype', label: 'Subtype' },
    { key: 'source_lineage', label: 'Source' },
    { key: 'currency', label: 'Currency' },
    { key: 'signed_amount', label: 'Signed amount', numeric: true },
    { key: 'quantity', label: 'Quantity', numeric: true },
    { key: 'register_id', label: 'Register' },
    { key: 'cashier_id', label: 'Cashier' },
    { key: 'salesperson_id', label: 'Salesperson' },
    { key: 'channel_id', label: 'Channel' },
    { key: 'tender_type', label: 'Tender type' },
    { key: 'detail_status', label: 'Detail status' },
    { key: 'return_reason', label: 'Return reason' },
    { key: 'transaction_number', label: 'Transaction number' },
    { key: 'business_date', label: 'Business date' },
  ],
  Line: [
    { key: 'item_code', label: 'Item code' },
    { key: 'quantity', label: 'Line quantity', numeric: true },
    { key: 'unit_price', label: 'Unit price', numeric: true },
    { key: 'gross_amount', label: 'Gross amount', numeric: true },
    { key: 'tax_code', label: 'Tax code' },
    { key: 'tax_amount', label: 'Tax amount', numeric: true },
    { key: 'return_reason', label: 'Return reason' },
  ],
  Tax: [
    { key: 'tax_code', label: 'Tax code' },
    { key: 'tax_name', label: 'Tax name' },
    { key: 'rate', label: 'Tax rate', numeric: true },
    { key: 'taxable_amount', label: 'Taxable amount', numeric: true },
    { key: 'tax_amount', label: 'Tax amount', numeric: true },
  ],
  Tender: [
    { key: 'tender_type', label: 'Tender type' },
    { key: 'amount', label: 'Amount', numeric: true },
    { key: 'reference', label: 'Reference' },
    { key: 'authorization', label: 'Authorization' },
  ],
  Discount: [
    { key: 'kind', label: 'Kind' },
    { key: 'code', label: 'Code' },
    { key: 'description', label: 'Description' },
    { key: 'amount', label: 'Amount', numeric: true },
  ],
};
const totalRealmRelationships: {
  realm: RestrictionRealm;
  label: string;
  relationship: string;
}[] = [
  {
    realm: 'Transaction',
    label: 'Transaction header',
    relationship: 'Base record: one header for each transaction.',
  },
  {
    realm: 'Line',
    label: 'Item lines',
    relationship: 'Each line belongs to one transaction; a transaction can have many lines.',
  },
  {
    realm: 'Tax',
    label: 'Tax details',
    relationship: 'Each tax row belongs to one transaction; a transaction can have many tax rows.',
  },
  {
    realm: 'Tender',
    label: 'Tender details',
    relationship: 'Each tender belongs to one transaction; a transaction can have many tenders.',
  },
  {
    realm: 'Discount',
    label: 'Discount details',
    relationship:
      'Each discount belongs to one transaction; a transaction can have many discounts.',
  },
];
const totalRealmOrder = totalRealmRelationships.map((item) => item.realm);
const totalRealmJoins = (realms: RestrictionRealm[]): TotalRealmJoin[] =>
  totalRealmOrder
    .filter((realm): realm is Exclude<RestrictionRealm, 'Transaction'> =>
      realm !== 'Transaction' && realms.includes(realm)
    )
    .map((realm) => ({ parent_realm: 'Transaction', child_realm: realm }));
const realmsForTotal = (total: TotalRevision | null): RestrictionRealm[] => {
  const referenced = [
    ...(total?.parameters ?? []).map((item) => item.realm),
    ...(total?.rollups ?? []).map((item) => item.realm),
    ...(total?.restrictions ?? []).map((item) => item.realm),
  ];
  const stored = total?.realms ?? [];
  return totalRealmOrder.filter(
    (realm) =>
      realm === 'Transaction' || stored.includes(realm) || referenced.includes(realm)
  );
};
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
  v === null ? '—' : formatNumber(Number(v), { maximumFractionDigits: 2 });
const measuredValue = (v: string | number) => formatNumber(Number(v), { maximumFractionDigits: 4 });
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

function calculation(row: TotalRevision, references: TotalRevision[] = []) {
  if (row.calculation_basis !== 'Combined') {
    const measureParameter = row.parameters?.[0];
    if (measureParameter) {
      return `${row.aggregation} of ${measureParameter.realm}.${measureParameter.field}${row.rollups?.length ? ` · grouped by ${row.rollups.map((item) => `${item.realm}.${item.field}`).join(', ')}` : ''}`;
    }
    return `${row.aggregation} of ${row.measure.toLowerCase()}${row.rollups?.length ? ` · grouped by ${row.rollups.map((item) => `${item.realm}.${item.field}`).join(', ')}` : ''}`;
  }
  const operators = row.combined_total_operators?.length
    ? row.combined_total_operators
    : [row.combined_operator || '?'];
  if (!row.combined_total_ids?.length) return `Combined (${operators.join(' ')})`;
  const names = new Map(references.map((total) => [total.id, total.name]));
  return (
    row.combined_total_ids
      .map(
        (id, index) =>
          `${index > 0 ? `${operators[index - 1] ?? '?'} ` : ''}${names.get(id) ?? 'Unavailable total'}`
      )
      .join(' ') +
    (row.rollups?.length
      ? ` · grouped by ${row.rollups.map((item) => `${item.realm}.${item.field}`).join(', ')}`
      : '')
  );
}

function filter(row: TotalRevision) {
  return [...row.transaction_types, row.tender_type].filter(Boolean).join(', ') || 'All records';
}

function restrictionSummary(row: TotalRevision) {
  if (!Array.isArray(row.restrictions) || row.restrictions.length === 0) return 'None';
  return (row.restrictions as Restriction[])
    .map((item) => {
      const operator = item.operator.replaceAll('_', ' ');
      const value = ['is_empty', 'not_empty'].includes(item.operator) ? '' : ` ${item.value}`;
      return `${item.realm}.${item.field} ${operator}${value}`;
    })
    .join(' AND ');
}

function dates(row: TotalRevision) {
  if (!row.start_date && !row.end_date) return 'Always';
  return `${row.start_date ?? 'Start'} to ${row.end_date ?? 'open-ended'}`;
}

function locationTraits(row: TotalRevision | null) {
  const traits = row?.location_traits;
  return Array.isArray(traits) && traits.every((trait) => typeof trait === 'string')
    ? [...traits].sort()
    : [];
}

export function TotalDefinitions({
  revisions,
  onChanged,
}: {
  revisions: TotalRevision[];
  onChanged: () => Promise<void>;
}) {
  const toast = useToast();
  const { me } = useMe();
  const groups = useMemo(() => groupRevisions(revisions), [revisions]);
  const totalsById = revisions;
  const [openName, setOpenName] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [packageError, setPackageError] = useState<string | null>(null);
  const [packagePreview, setPackagePreview] = useState<{
    fileName: string;
    bundle: Record<string, unknown>;
    result: {
      valid: boolean;
      preview_token: string | null;
      errors: { row: number; message: string }[];
      counts: { worksheets: number; skipped_protected: number };
      definitions: {
        row: number;
        name: string;
        source_revision: number;
        target_revision: number;
        action: string;
      }[];
    };
  } | null>(null);
  const [importing, setImporting] = useState(false);
  const packageInput = useRef<HTMLInputElement>(null);
  const open = groups.find((group) => group.name === openName) ?? null;
  const drafts = groups.filter((group) => group.draft?.status === DRAFT).length;
  const awaiting = groups.filter((group) => group.draft?.status === PENDING).length;

  const downloadDefinitionPackage = async () => {
    setExporting(true);
    setPackageError(null);
    try {
      const { data } = await api.get('/api/total-definitions/package');
      const date = new Date().toISOString().slice(0, 10);
      downloadBlob(
        `audit-total-definitions-${date}.json`,
        new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' })
      );
      toast('Total definition snapshot downloaded.', 'success');
    } catch {
      setPackageError('Could not download total definitions.');
    } finally {
      setExporting(false);
    }
  };

  const previewDefinitionPackage = async (file?: File) => {
    if (!file) return;
    setImporting(true);
    setPackageError(null);
    setPackagePreview(null);
    try {
      if (file.size > 2_000_000) throw new Error('Snapshots must be smaller than 2 MB.');
      const bundle = JSON.parse(await file.text()) as Record<string, unknown>;
      const { data } = await api.post('/api/total-definitions/package/preview', bundle);
      setPackagePreview({ fileName: file.name, bundle, result: data });
    } catch (cause) {
      setPackageError(
        cause instanceof SyntaxError
          ? 'The selected file is not valid JSON.'
          : cause instanceof Error && !('response' in cause)
            ? cause.message
            : apiError(cause, 'Could not preview this total definition snapshot.')
      );
    } finally {
      setImporting(false);
      if (packageInput.current) packageInput.current.value = '';
    }
  };

  const applyDefinitionPackage = async () => {
    if (!packagePreview?.result.valid || !packagePreview.result.preview_token) return;
    setImporting(true);
    setPackageError(null);
    try {
      const { data } = await api.post('/api/total-definitions/package/apply', {
        ...packagePreview.bundle,
        preview_token: packagePreview.result.preview_token,
      });
      setPackagePreview(null);
      await onChanged();
      toast(`Imported ${data.imported.length} total worksheet(s).`, 'success');
    } catch (cause) {
      setPackageError(
        apiError(cause, 'Could not import this snapshot. Preview it again and retry.')
      );
    } finally {
      setImporting(false);
    }
  };

  return (
    <Card title="Audit total definitions">
      {packageError && (
        <Alert tone="error" onDismiss={() => setPackageError(null)}>
          {packageError}
        </Alert>
      )}
      <p>
        Live revisions are calculated during Store Day re-totaling. A change is saved as a draft and
        only goes live after its impact on store days is previewed, submitted, and approved by a
        second Finance user. Rules reference <b>Configured: [definition name]</b>.
      </p>
      <div className="cluster mt-sm">
        <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
          New total
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={!revisions.length || exporting}
          onClick={() => void downloadDefinitionPackage()}
        >
          {exporting ? 'Preparing snapshot…' : 'Download all revisions'}
        </Button>
        {(me?.role === 'finance' || me?.role === 'admin') && (
          <Button
            size="sm"
            variant="secondary"
            disabled={importing}
            onClick={() => packageInput.current?.click()}
          >
            {importing ? 'Preparing import…' : 'Import snapshot'}
          </Button>
        )}
        <input
          ref={packageInput}
          type="file"
          accept=".json,application/json"
          className="sr-only"
          aria-label="Choose an audit total definitions snapshot"
          onChange={(event) => void previewDefinitionPackage(event.currentTarget.files?.[0])}
        />
        {drafts > 0 && (
          <span className="page-sub">
            {drafts} draft{drafts === 1 ? '' : 's'} waiting to be previewed and submitted.
          </span>
        )}
        {awaiting > 0 && <span className="page-sub">{awaiting} awaiting a second approver.</span>}
      </div>
      {packagePreview && (
        <div className="stack-sm mt-md">
          <h3>Import preview: {packagePreview.fileName}</h3>
          <p>
            {packagePreview.result.counts.worksheets} worksheet(s) will be created.{' '}
            {packagePreview.result.counts.skipped_protected} system-required definition(s) will be
            skipped. All imported totals remain inactive until reviewed and approved.
          </p>
          {packagePreview.result.errors.length > 0 && (
            <Alert tone="error">
              {packagePreview.result.errors.map((item) => (
                <div key={`${item.row}-${item.message}`}>
                  Row {item.row}: {item.message}
                </div>
              ))}
            </Alert>
          )}
          {packagePreview.result.definitions.length > 0 && (
            <DataTable
              rows={packagePreview.result.definitions}
              rowKey={(row) => `${row.row}-${row.name}`}
              columns={[
                { key: 'name', header: 'Total', primary: true, render: (row) => row.name },
                {
                  key: 'revision',
                  header: 'Revisions',
                  render: (row) => `${row.source_revision} → ${row.target_revision}`,
                },
                { key: 'action', header: 'Action', render: (row) => row.action },
              ]}
            />
          )}
          <div className="cluster">
            <Button
              size="sm"
              variant="primary"
              disabled={
                !packagePreview.result.valid ||
                importing ||
                (me?.role !== 'finance' && me?.role !== 'admin')
              }
              onClick={() => void applyDefinitionPackage()}
            >
              {importing ? 'Importing…' : 'Create worksheets'}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={importing}
              onClick={() => setPackagePreview(null)}
            >
              Cancel
            </Button>
            {me?.role !== 'finance' && me?.role !== 'admin' && (
              <span className="page-sub">Finance role is required to create worksheets.</span>
            )}
          </div>
        </div>
      )}
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
              render: (g) => calculation((g.draft ?? g.live)!, totalsById),
            },
            {
              key: 'source',
              header: 'Source',
              render: (g) => (g.draft ?? g.live)!.source_system || 'Any',
            },
            {
              key: 'restrictions',
              header: 'Restrictions',
              render: (g) => {
                const count = Array.isArray((g.draft ?? g.live)!.restrictions)
                  ? ((g.draft ?? g.live)!.restrictions as unknown[]).length
                  : 0;
                return count ? `${count} condition${count === 1 ? '' : 's'}` : 'None';
              },
            },
            {
              key: 'export_usage',
              header: 'Export usage',
              render: (g) => {
                const usages = (g.draft ?? g.live)!.usages ?? [];
                return usages.length
                  ? `${usages.length} destination${usages.length === 1 ? '' : 's'}`
                  : 'All destinations';
              },
            },
            {
              key: 'report_usage',
              header: 'Flash reports',
              render: (g) => {
                const usages = (g.draft ?? g.live)!.report_usages ?? [];
                return usages.length
                  ? usages
                      .map((usage) =>
                        usage === 'flash_totals' ? 'Flash Totals' : 'Flash Sales YTD'
                      )
                      .join(', ')
                  : 'None';
              },
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
          availableTotals={groups.flatMap((item) =>
            item.live && item.live.active ? [item.live] : []
          )}
          onClose={() => setOpenName(null)}
          onChanged={onChanged}
        />
      )}
      {creating && (
        <Drawer title="New total" onClose={() => setCreating(false)}>
          <DraftForm
            base={null}
            availableTotals={groups.flatMap((group) =>
              group.live && group.live.active ? [group.live] : []
            )}
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
  availableTotals,
  onClose,
  onChanged,
}: {
  group: TotalGroup;
  availableTotals: TotalRevision[];
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
  const [testStore, setTestStore] = useState('');
  const [testDate, setTestDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [calculationPreview, setCalculationPreview] = useState<CalculationPreview | null>(null);
  const [calculationBusy, setCalculationBusy] = useState(false);
  const [calculationError, setCalculationError] = useState<string | null>(null);
  const draft = group.draft;
  const previewTarget = draft ?? group.live;
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

  const runCalculationPreview = async () => {
    if (!previewTarget || !testStore || !testDate) return;
    setCalculationBusy(true);
    setCalculationError(null);
    setCalculationPreview(null);
    try {
      const { data } = await api.get<CalculationPreview>(
        `/api/total-definitions/${previewTarget.id}/preview`,
        { params: { store_id: testStore, business_date: testDate } }
      );
      setCalculationPreview(data);
    } catch (cause) {
      setCalculationError(
        detail(cause, 'Could not calculate this total for the selected Store Day.')
      );
    } finally {
      setCalculationBusy(false);
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
          availableTotals={availableTotals}
          onCancel={() => setEditing(false)}
          onSaved={async () => {
            setImpact(null);
            setCalculationPreview(null);
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
            references={availableTotals}
          />
        )}
        <RevisionSummary
          title={group.live ? `Live r${group.live.revision}` : 'Not live yet'}
          row={group.live}
          references={availableTotals}
        />
        {previewTarget && (
          <section className="stack stack-sm">
            <h3>Test calculation</h3>
            <p className="hint">
              Read-only calculation using current Store Day data and this saved revision. Save the
              worksheet first if you have changed its fields.
            </p>
            {calculationError && (
              <Alert tone="error" onDismiss={() => setCalculationError(null)}>
                {calculationError}
              </Alert>
            )}
            <div className="form-row">
              <StoreSelect
                value={testStore}
                label="Test store"
                onChange={(value) => {
                  setTestStore(value);
                  setCalculationPreview(null);
                }}
              />
              <Field label="Business date">
                <input
                  type="date"
                  value={testDate}
                  onChange={(event) => {
                    setTestDate(event.target.value);
                    setCalculationPreview(null);
                  }}
                />
              </Field>
              <Button
                size="sm"
                disabled={calculationBusy || !testStore || !testDate}
                onClick={() => void runCalculationPreview()}
              >
                {calculationBusy ? 'Calculating…' : 'Calculate total'}
              </Button>
            </div>
            {calculationPreview && (
              <>
                <MetricGrid>
                  <MetricTile
                    label="Calculated value"
                    value={
                      calculationPreview.currency
                        ? money(calculationPreview.value, calculationPreview.currency)
                        : measuredValue(calculationPreview.value)
                    }
                  />
                  <MetricTile
                    label="Records counted"
                    value={formatNumber(calculationPreview.record_count ?? 0)}
                  />
                  <MetricTile
                    label="Revision"
                    value={`r${calculationPreview.definition.revision}`}
                  />
                </MetricGrid>
                <DataTable
                  columns={
                    [
                      {
                        key: 'dimension',
                        header: previewTarget.level === 'Store' ? 'Total' : previewTarget.level,
                        primary: true,
                        render: (row) => row.dimension ?? 'Whole store',
                      },
                      {
                        key: 'rollup_key',
                        header: 'Roll-up group',
                        render: (row) => {
                          if (!row.rollup_key) return '—';
                          try {
                            return (JSON.parse(row.rollup_key) as [string, string, unknown][])
                              .map(([realm, field, value]) => `${realm}.${field}: ${value ?? '—'}`)
                              .join(' · ');
                          } catch {
                            return row.rollup_key;
                          }
                        },
                      },
                      {
                        key: 'value',
                        header: 'Value',
                        numeric: true,
                        render: (row) =>
                          calculationPreview.currency
                            ? money(row.value, calculationPreview.currency)
                            : measuredValue(row.value),
                      },
                    ] satisfies Column<CalculationPreview['dimensions'][number]>[]
                  }
                  rows={calculationPreview.dimensions}
                  rowKey={(row) => `${row.dimension ?? 'store'}|${row.rollup_key ?? ''}`}
                  emptyTitle="No matching records"
                  emptyHint="The selected filters produced no parameter values for this Store Day."
                />
              </>
            )}
          </section>
        )}
        {pending && (
          <Alert tone={ownSubmission ? 'info' : 'warning'}>
            Submitted by {draft?.submitted_by ?? 'someone'}
            {draft?.submitted_at ? ` on ${formatDateTime(draft.submitted_at)}` : ''}.{' '}
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
  references,
}: {
  title: string;
  row: TotalRevision | null;
  compare?: TotalRevision | null;
  references: TotalRevision[];
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
    ['Calculation', (r) => calculation(r, references)],
    ['Source', (r) => r.source_system || 'Any'],
    ['Records', filter],
    ['Dataset restrictions', restrictionSummary],
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
  availableTotals,
  onCancel,
  onSaved,
}: {
  base: TotalRevision | null;
  availableTotals: TotalRevision[];
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
    calculation_basis: base?.calculation_basis ?? 'Raw',
    combined_total_ids: base?.combined_total_ids ?? [],
    combined_total_operators: base?.combined_total_operators?.length
      ? base.combined_total_operators
      : base?.combined_operator
        ? Array(Math.max(0, (base.combined_total_ids?.length ?? 0) - 1)).fill(
            base.combined_operator
          )
        : [],
    parameters: (Array.isArray(base?.parameters) ? base.parameters : []) as {
      realm: RestrictionRealm;
      field: string;
    }[],
    rollups: (Array.isArray(base?.rollups) ? base.rollups : []) as {
      realm: RestrictionRealm;
      field: string;
    }[],
    realms: realmsForTotal(base),
    joins: totalRealmJoins(realmsForTotal(base)),
    usages: (Array.isArray(base?.usages) ? base.usages : []) as { destination_id: string }[],
    report_usages: (Array.isArray(base?.report_usages) ? base.report_usages : []) as (
      | 'flash_totals'
      | 'flash_sales_ytd'
    )[],
    restrictions: (Array.isArray(base?.restrictions) ? base.restrictions : []) as Restriction[],
  });
  const [destinations, setDestinations] = useState<ExportDestination[]>([]);
  const [destinationsLoaded, setDestinationsLoaded] = useState(false);
  const [destinationsError, setDestinationsError] = useState<string | null>(null);
  useEffect(() => {
    api
      .get<ExportDestination[]>('/api/exports/destinations')
      .then(({ data }) => {
        setDestinations(data);
        setDestinationsLoaded(true);
        setDestinationsError(null);
      })
      .catch((cause) => setDestinationsError(detail(cause, 'Could not load export destinations.')));
  }, []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const combined = form.calculation_basis === 'Combined';
  const realmIsReferenced = (realm: RestrictionRealm) =>
    form.parameters.some((item) => item.realm === realm) ||
    form.rollups.some((item) => item.realm === realm) ||
    form.restrictions.some((item) => item.realm === realm);
  const setRealmSelected = (realm: RestrictionRealm, selected: boolean) => {
    if (realm === 'Transaction') return;
    const realms = selected
      ? totalRealmOrder.filter((item) => form.realms.includes(item) || item === realm)
      : form.realms.filter((item) => item !== realm);
    setForm({ ...form, realms, joins: totalRealmJoins(realms) });
  };
  const expectedTraits = JSON.stringify(locationTraits(base));
  const componentChoices = availableTotals.filter(
    (total) =>
      total.name !== form.name &&
      total.level === form.level &&
      total.measure === form.measure &&
      total.aggregation === form.aggregation &&
      JSON.stringify(total.parameters ?? []) === JSON.stringify(form.parameters) &&
      JSON.stringify(total.rollups ?? []) === JSON.stringify(form.rollups) &&
      JSON.stringify(locationTraits(total)) === expectedTraits
  );
  const formulaValid =
    !combined ||
    (form.combined_total_ids.length >= 2 &&
      form.combined_total_ids.every((id) => componentChoices.some((total) => total.id === id)) &&
      form.combined_total_operators.length === form.combined_total_ids.length - 1);
  const restrictionsValid = form.restrictions.every(
    (restriction) =>
      ['is_empty', 'not_empty'].includes(restriction.operator) ||
      restriction.value.trim().length > 0
  );
  const parameter = form.parameters[0];
  const validRollupRealms = combined
    ? Object.keys(restrictionFields)
    : [...new Set(['Transaction', parameter?.realm ?? 'Transaction'])];
  const rollupOptions = validRollupRealms.flatMap((realm) =>
    restrictionFields[realm as RestrictionRealm].map((field) => ({
      value: `${realm}.${field.key}`,
      label: `${realm} · ${field.label}`,
    }))
  );
  const parameterFields = parameter ? restrictionFields[parameter.realm] : [];
  const parameterField = parameterFields.find((field) => field.key === parameter?.field);
  const parameterValid =
    !parameter || form.aggregation === 'Count' || parameterField?.numeric === true;
  const configuredRealms = new Set<RestrictionRealm>(form.realms);
  const parameterOptions = Object.entries(restrictionFields)
    .filter(([realm]) => form.realms.includes(realm as RestrictionRealm))
    .flatMap(([realm, fields]) =>
      fields
        .filter((field) => form.aggregation === 'Count' || field.numeric)
        .map((field) => ({
          value: `${realm}.${field.key}`,
          label: `${realm} · ${field.label}`,
        }))
    );
  const addComponent = () => {
    const next = componentChoices.find((total) => !form.combined_total_ids.includes(total.id));
    if (!next || form.combined_total_ids.length >= 20) return;
    setForm({
      ...form,
      combined_total_ids: [...form.combined_total_ids, next.id],
      combined_total_operators:
        form.combined_total_ids.length > 0
          ? [...form.combined_total_operators, '+']
          : form.combined_total_operators,
    });
  };
  const removeLastComponent = () => {
    if (form.combined_total_ids.length <= 1) return;
    setForm({
      ...form,
      combined_total_ids: form.combined_total_ids.slice(0, -1),
      combined_total_operators: form.combined_total_operators.slice(0, -1),
    });
  };
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
      calculation_basis: form.calculation_basis,
      parameters: combined ? [] : form.parameters,
      realms: combined ? [] : form.realms,
      joins: combined ? [] : form.joins,
      rollups: form.rollups,
      usages: form.usages,
      report_usages: form.report_usages,
      combined_total_ids: combined ? form.combined_total_ids : [],
      combined_total_operators: combined ? form.combined_total_operators : [],
      restrictions: combined
        ? []
        : form.restrictions.map((item) => ({
            ...item,
            value: ['is_empty', 'not_empty'].includes(item.operator) ? null : item.value,
          })),
      combined_operator:
        combined && form.combined_total_ids.length === 2
          ? (form.combined_total_operators[0] ?? base?.combined_operator ?? null)
          : null,
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
      <Field label="Calculation basis">
        <select
          value={form.calculation_basis}
          onChange={(event) =>
            setForm({
              ...form,
              calculation_basis: event.target.value,
              ...(event.target.value === 'Combined'
                ? { parameters: [], restrictions: [], realms: [], joins: [] }
                : {
                    realms: ['Transaction'],
                    joins: [],
                    rollups: form.rollups.filter((item) => item.realm === 'Transaction'),
                  }),
            })
          }
        >
          <option value="Raw">Raw transaction data</option>
          <option value="Combined">Formula from other totals</option>
        </select>
      </Field>
      {combined ? (
        <fieldset className="stack stack-sm">
          <legend>Combined-total formula</legend>
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
          <p className="hint">
            Choose 2 to 20 compatible live totals. Operators are evaluated left to right, so each
            step is explicit in the formula.
          </p>
          {form.combined_total_ids.map((id, index) => {
            const selected = componentChoices.find((total) => total.id === id);
            return (
              <div className="form-row" key={`${index}-${id}`}>
                {index > 0 && (
                  <Field label={`Operator ${index}`}>
                    <select
                      value={form.combined_total_operators[index - 1] ?? '+'}
                      onChange={(event) => {
                        const operators = [...form.combined_total_operators];
                        operators[index - 1] = event.target.value;
                        setForm({ ...form, combined_total_operators: operators });
                      }}
                    >
                      <option value="+">Add (+)</option>
                      <option value="-">Subtract (−)</option>
                    </select>
                  </Field>
                )}
                <Field label={`Total ${index + 1}`}>
                  <select
                    value={id}
                    onChange={(event) => {
                      const identifiers = [...form.combined_total_ids];
                      identifiers[index] = event.target.value;
                      setForm({ ...form, combined_total_ids: identifiers });
                    }}
                  >
                    {!selected && <option value={id}>Unavailable or retired total</option>}
                    {componentChoices.map((total) => (
                      <option
                        key={total.id}
                        value={total.id}
                        disabled={total.id !== id && form.combined_total_ids.includes(total.id)}
                      >
                        {total.name} · {total.level}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
            );
          })}
          {componentChoices.length < 2 && (
            <Alert tone="warning">At least two other live totals are required for a formula.</Alert>
          )}
          <div className="cluster">
            <Button
              size="sm"
              variant="secondary"
              disabled={
                form.combined_total_ids.length >= 20 ||
                componentChoices.length <= form.combined_total_ids.length
              }
              onClick={addComponent}
            >
              Add total
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={form.combined_total_ids.length <= 2}
              onClick={removeLastComponent}
            >
              Remove last total
            </Button>
          </div>
        </fieldset>
      ) : (
        <>
          <fieldset className="stack stack-sm">
            <legend>Data realms and joins</legend>
            <p className="hint">
              Transaction is the base realm. Detail realms link back to that transaction. Conditions
              in separate detail realms are checked independently, so matching lines and tenders do
              not multiply one another or the transaction amount.
            </p>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 15rem), 1fr))',
                gap: 'var(--space-sm)',
              }}
            >
              {totalRealmRelationships.map((item) => {
                const selected = configuredRealms.has(item.realm);
                const referenced = realmIsReferenced(item.realm);
                return (
                  <div
                    key={item.realm}
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 'var(--space-xs)',
                      padding: 'var(--space-sm)',
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--radius-md)',
                      background: 'var(--bg-secondary)',
                    }}
                  >
                    <div className="cluster" style={{ justifyContent: 'space-between' }}>
                      <label className="cluster" style={{ cursor: 'pointer' }}>
                        <input
                          type="checkbox"
                          checked={selected}
                          disabled={item.realm === 'Transaction' || (selected && referenced)}
                          onChange={(event) => setRealmSelected(item.realm, event.target.checked)}
                        />
                        <strong>{item.label}</strong>
                      </label>
                      <Badge variant={selected ? 'primary' : 'secondary'} size="sm">
                        {item.realm === 'Transaction' ? 'Base' : selected ? 'Selected' : 'Available'}
                      </Badge>
                    </div>
                    <small className="page-sub">{item.relationship}</small>
                    {item.realm !== 'Transaction' && selected && (
                      <small className="act-sub">Join path: {item.realm} → Transaction</small>
                    )}
                    {selected && referenced && item.realm !== 'Transaction' && (
                      <small className="act-sub">
                        Remove its selected fields before deselecting this realm.
                      </small>
                    )}
                  </div>
                );
              })}
            </div>
          </fieldset>
          <div className="form-row">
            <Field label="Aggregation">
              <select
                value={form.aggregation}
                onChange={(event) =>
                  setForm({
                    ...form,
                    aggregation: event.target.value,
                    measure: event.target.value === 'Count' ? 'Amount' : form.measure,
                    parameters:
                      event.target.value === 'Sum' && parameter && !parameterField?.numeric
                        ? []
                        : form.parameters,
                  })
                }
              >
                <option>Sum</option>
                <option>Count</option>
              </select>
            </Field>
            <Field
              label="Parameter to sum or count"
              hint="Leave on default to use transaction amount or tax."
            >
              <select
                value={parameter ? `${parameter.realm}.${parameter.field}` : ''}
                onChange={(event) => {
                  const [realm, field] = event.target.value.split('.');
                  setForm({
                    ...form,
                    measure: event.target.value ? 'Amount' : form.measure,
                    parameters: event.target.value
                      ? [{ realm: realm as RestrictionRealm, field }]
                      : [],
                    rollups: form.rollups.filter(
                      (item) => item.realm === 'Transaction' || item.realm === realm
                    ),
                  });
                }}
              >
                <option value="">Default transaction amount or tax</option>
                {parameterOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </Field>
            {!parameter && (
              <Field label="Default measure">
                <select value={form.measure} onChange={set('measure')}>
                  <option>Amount</option>
                  {form.aggregation === 'Sum' && <option>Tax</option>}
                </select>
              </Field>
            )}
          </div>
          {!parameterValid && (
            <Alert tone="error">Sum requires a numeric parameter. Choose another field.</Alert>
          )}
          <Field label="Source system" hint="Leave empty for any source.">
            <input value={form.source_system} onChange={set('source_system')} />
          </Field>
          <Field label="Transaction types" hint="Comma-separated, for example Sale, Return.">
            <input value={form.transaction_types} onChange={set('transaction_types')} />
          </Field>
          <Field label="Tender type" hint="Leave empty for every tender.">
            <input value={form.tender_type} onChange={set('tender_type')} />
          </Field>
          <fieldset className="stack stack-sm">
            <legend>Dataset restrictions</legend>
            <p className="hint">
              Conditions in each selected realm use AND. Each related realm must have at least one
              matching record; detail records from separate realms are not multiplied into the
              total.
            </p>
            {form.restrictions.map((restriction, index) => {
              const realmFields = restrictionFields[restriction.realm];
              const selectedField = realmFields.find((field) => field.key === restriction.field);
              const operators = selectedField?.numeric
                ? ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'is_empty', 'not_empty']
                : ['eq', 'ne', 'contains', 'is_empty', 'not_empty'];
              return (
                <div className="form-row" key={`restriction-${index}`}>
                  <Field label={`Realm ${index + 1}`}>
                    <select
                      value={restriction.realm}
                      onChange={(event) => {
                        const realm = event.target.value as RestrictionRealm;
                        const next = [...form.restrictions];
                        next[index] = {
                          realm,
                          field: restrictionFields[realm][0].key,
                          operator: restrictionFields[realm][0].numeric ? 'gt' : 'eq',
                          value: '',
                        };
                        setForm({ ...form, restrictions: next });
                      }}
                    >
                      {form.realms.map((realm) => (
                        <option key={realm}>{realm}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Field">
                    <select
                      value={restriction.field}
                      onChange={(event) => {
                        const field = realmFields.find((item) => item.key === event.target.value)!;
                        const next = [...form.restrictions];
                        next[index] = {
                          ...restriction,
                          field: field.key,
                          operator: field.numeric ? 'gt' : 'eq',
                          value: '',
                        };
                        setForm({ ...form, restrictions: next });
                      }}
                    >
                      {realmFields.map((field) => (
                        <option key={field.key} value={field.key}>
                          {field.label}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Condition">
                    <select
                      value={restriction.operator}
                      onChange={(event) => {
                        const next = [...form.restrictions];
                        next[index] = {
                          ...restriction,
                          operator: event.target.value as Restriction['operator'],
                          value: '',
                        };
                        setForm({ ...form, restrictions: next });
                      }}
                    >
                      {operators.map((operator) => (
                        <option key={operator} value={operator}>
                          {operator.replaceAll('_', ' ')}
                        </option>
                      ))}
                    </select>
                  </Field>
                  {!['is_empty', 'not_empty'].includes(restriction.operator) && (
                    <Field label="Value">
                      <input
                        type={selectedField?.numeric ? 'number' : 'text'}
                        step="any"
                        value={restriction.value}
                        onChange={(event) => {
                          const next = [...form.restrictions];
                          next[index] = { ...restriction, value: event.target.value };
                          setForm({ ...form, restrictions: next });
                        }}
                      />
                    </Field>
                  )}
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      setForm({
                        ...form,
                        restrictions: form.restrictions.filter((_, i) => i !== index),
                      })
                    }
                  >
                    Remove
                  </Button>
                </div>
              );
            })}
            <Button
              size="sm"
              variant="secondary"
              disabled={form.restrictions.length >= 100}
              onClick={() =>
                (() => {
                  const realm = form.realms.find((item) => item !== 'Transaction') ?? 'Transaction';
                  const field = restrictionFields[realm][0];
                  setForm({
                    ...form,
                    restrictions: [
                      ...form.restrictions,
                      {
                        realm,
                        field: field.key,
                        operator: field.numeric ? 'gt' : 'eq',
                        value: '',
                      },
                    ],
                  });
                })()
              }
            >
              Add restriction
            </Button>
          </fieldset>
        </>
      )}
      <fieldset className="stack stack-sm">
        <legend>Roll-up groups</legend>
        <p className="hint">
          Split this total into up to three groups. The balancing level remains the store, register,
          cashier, or tender dimension used for reconciliation.
        </p>
        {form.rollups.map((rollup, index) => (
          <div className="form-row" key={`rollup-${index}`}>
            <Field label={`Group field ${index + 1}`}>
              <select
                value={`${rollup.realm}.${rollup.field}`}
                onChange={(event) => {
                  const [realm, field] = event.target.value.split('.') as [
                    RestrictionRealm,
                    string,
                  ];
                  const next = [...form.rollups];
                  next[index] = { realm, field };
                  setForm({ ...form, rollups: next });
                }}
              >
                {rollupOptions.map((option) => (
                  <option
                    key={option.value}
                    value={option.value}
                    disabled={form.rollups.some(
                      (selected, selectedIndex) =>
                        selectedIndex !== index &&
                        `${selected.realm}.${selected.field}` === option.value
                    )}
                  >
                    {option.label}
                  </option>
                ))}
              </select>
            </Field>
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                setForm({ ...form, rollups: form.rollups.filter((_, i) => i !== index) })
              }
            >
              Remove
            </Button>
          </div>
        ))}
        <Button
          size="sm"
          variant="secondary"
          disabled={
            form.rollups.length >= 3 ||
            rollupOptions.every((item) =>
              form.rollups.some((selected) => `${selected.realm}.${selected.field}` === item.value)
            )
          }
          onClick={() => {
            const next = rollupOptions.find(
              (item) =>
                !form.rollups.some(
                  (selected) => `${selected.realm}.${selected.field}` === item.value
                )
            );
            if (!next) return;
            const [realm, field] = next.value.split('.') as [RestrictionRealm, string];
            setForm({ ...form, rollups: [...form.rollups, { realm, field }] });
          }}
        >
          Add group field
        </Button>
      </fieldset>
      <fieldset className="stack stack-sm">
        <legend>Export usage</legend>
        <p className="hint">
          Choose which destinations receive this total. With no destinations selected, the total
          remains available to every destination.
        </p>
        {destinationsError && <Alert tone="warning">{destinationsError}</Alert>}
        {!destinationsLoaded && !destinationsError && <p className="hint">Loading destinations…</p>}
        {destinations.map((destination) => (
          <label className="cluster" key={destination.id}>
            <input
              type="checkbox"
              checked={form.usages.some((usage) => usage.destination_id === destination.id)}
              onChange={(event) =>
                setForm({
                  ...form,
                  usages: event.target.checked
                    ? [...form.usages, { destination_id: destination.id }]
                    : form.usages.filter((usage) => usage.destination_id !== destination.id),
                })
              }
            />
            {destination.name}
            {!destination.enabled ? ' · disabled' : ''}
          </label>
        ))}
        {destinationsLoaded &&
          form.usages
            .filter(
              (usage) =>
                !destinations.some((destination) => destination.id === usage.destination_id)
            )
            .map((usage) => (
              <Alert key={usage.destination_id} tone="warning">
                A configured destination is unavailable ({usage.destination_id}). Remove it before
                saving this revision.
              </Alert>
            ))}
      </fieldset>
      <fieldset className="stack stack-sm">
        <legend>Flash report usage</legend>
        <p className="hint">
          Include this calculated total in the selected Flash reports. Only current, non-stale
          store-day totals are reported.
        </p>
        {([
          ['flash_totals', 'Flash Totals'],
          ['flash_sales_ytd', 'Flash Sales Year to Date'],
        ] as const).map(([usage, label]) => (
          <label className="cluster" key={usage}>
            <input
              type="checkbox"
              checked={form.report_usages.includes(usage)}
              onChange={(event) =>
                setForm({
                  ...form,
                  report_usages: event.target.checked
                    ? [...form.report_usages, usage]
                    : form.report_usages.filter((item) => item !== usage),
                })
              }
            />
            {label}
          </label>
        ))}
      </fieldset>
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
          disabled={
            saving || !form.name.trim() || !formulaValid || !restrictionsValid || !parameterValid
          }
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
