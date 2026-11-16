'use client';

import { formatNumber } from '@/lib/preferences';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, money } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { apiError } from '@/lib/storeDays';
import { useMe } from '@/components/MeProvider';
import { useToast } from '@/components/Feedback';
import { StoreSelect } from '@/components/StoreSelect';
import ControlsTable from '@/components/settings/ControlsTable';
import { Alert, Button, Card, DataTable, Field, MetricGrid, MetricTile } from '@/components/ui';
import type { Column } from '@/components/ui';

type FieldName =
  | 'business_date'
  | 'transaction_type'
  | 'transaction_subtype'
  | 'source_lineage'
  | 'currency'
  | 'signed_amount'
  | 'quantity'
  | 'register_id'
  | 'cashier_id'
  | 'salesperson_id'
  | 'channel_id'
  | 'tender_type'
  | 'detail_status'
  | 'return_reason'
  | 'transaction_number';
type Operator = 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte' | 'contains' | 'is_empty' | 'not_empty';
type Condition = { field: FieldName; operator: Operator; value: string };
type TransactionRule = {
  id: string;
  name: string;
  error_code: string;
  record_type: string | null;
  conditions: Condition[];
  match_mode: 'Match' | 'No Match';
  evaluation_scope: 'Transaction' | 'StoreDayAbsence';
  start_date: string | null;
  end_date: string | null;
  location_traits: string[];
  execution_order: number;
  stop_processing: boolean;
  status: 'Worksheet' | 'Submitted' | 'Active' | 'Disabled';
  revision: number;
  owner: string | null;
  submitted_by: string | null;
  approved_by: string | null;
};
type ErrorCode = { code: string; label: string; active?: boolean };
type Preview = {
  error_code: string;
  evaluated: number;
  would_trigger: number;
  evaluation_scope: 'Transaction' | 'StoreDayAbsence';
  sample_limit: number;
  samples: {
    transaction_id: string | null;
    store_id: string;
    business_date: string;
    record_type: string;
    amount: string;
    currency: string | null;
    register_id: string | null;
    cashier_id: string | null;
  }[];
};
type RulePackage = {
  format: 'zetsa.transaction-audit-rules';
  version: 1;
  exported_at?: string;
  rules: Record<string, unknown>[];
};
type RulePackagePreview = {
  valid: boolean;
  preview_token?: string;
  errors: { row: number; message: string }[];
  rows: {
    row: number;
    name: string;
    error_code: string;
    condition_count: number;
    action: string;
  }[];
  counts: { worksheets: number };
};

const fields: { key: FieldName; label: string; numeric?: boolean }[] = [
  { key: 'business_date', label: 'Business date' },
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
];
const textOperators: Operator[] = ['eq', 'ne', 'contains', 'is_empty', 'not_empty'];
const numberOperators: Operator[] = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'is_empty', 'not_empty'];
const operatorLabels: Record<Operator, string> = {
  eq: 'equals',
  ne: 'does not equal',
  gt: 'is greater than',
  gte: 'is at least',
  lt: 'is less than',
  lte: 'is at most',
  contains: 'contains',
  is_empty: 'is empty',
  not_empty: 'is present',
};
const blankCondition = (): Condition => ({ field: 'signed_amount', operator: 'gt', value: '1000' });
type RuleDraft = {
  name: string;
  error_code: string;
  record_type: string;
  conditions: Condition[];
  match_mode: 'Match' | 'No Match';
  evaluation_scope: 'Transaction' | 'StoreDayAbsence';
  start_date: string;
  end_date: string;
  location_traits: string[];
  execution_order: number;
  stop_processing: boolean;
  owner: string;
};
const blankRule = (): RuleDraft => ({
  name: '',
  error_code: '',
  record_type: '',
  conditions: [blankCondition()],
  match_mode: 'Match',
  evaluation_scope: 'Transaction',
  start_date: '',
  end_date: '',
  location_traits: [] as string[],
  execution_order: 100,
  stop_processing: false,
  owner: '',
});

export function TransactionRules({ locationTraitOptions }: { locationTraitOptions: string[] }) {
  const { me } = useMe();
  const toast = useToast();
  const [rules, setRules] = useState<TransactionRule[]>([]);
  const [errorCodes, setErrorCodes] = useState<ErrorCode[]>([]);
  const [draft, setDraft] = useState(blankRule);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previewRule, setPreviewRule] = useState<TransactionRule | null>(null);
  const [previewStore, setPreviewStore] = useState('');
  const [previewFrom, setPreviewFrom] = useState(() =>
    new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10)
  );
  const [previewTo, setPreviewTo] = useState(() => new Date().toISOString().slice(0, 10));
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewExporting, setPreviewExporting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [packagePreview, setPackagePreview] = useState<{
    fileName: string;
    bundle: RulePackage;
    result: RulePackagePreview;
  } | null>(null);
  const [importing, setImporting] = useState(false);
  const packageInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [ruleResponse, errorResponse] = await Promise.all([
        api.get('/api/transaction-audit-rules'),
        api.get('/api/audit-errors'),
      ]);
      setRules(ruleResponse.data);
      setErrorCodes(errorResponse.data.items.filter((item: ErrorCode) => item.active !== false));
      setError(null);
    } catch (cause) {
      setError(apiError(cause, 'Could not load transaction rules.'));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const saveDraft = async () => {
    setBusy(true);
    setError(null);
    const body = {
      ...draft,
      record_type: draft.record_type || null,
      start_date: draft.start_date || null,
      end_date: draft.end_date || null,
      owner: draft.owner || null,
      conditions: draft.conditions.map((condition) => ({
        ...condition,
        value: ['is_empty', 'not_empty'].includes(condition.operator) ? null : condition.value,
      })),
    };
    try {
      if (editingId) await api.put(`/api/transaction-audit-rules/${editingId}`, body);
      else await api.post('/api/transaction-audit-rules', body);
      toast(
        editingId ? 'Transaction rule worksheet saved.' : 'Transaction rule worksheet created.',
        'success'
      );
      setDraft(blankRule());
      setEditingId(null);
      await load();
    } catch (cause) {
      setError(apiError(cause, 'Could not save the transaction rule.'));
    } finally {
      setBusy(false);
    }
  };

  const act = async (
    rule: TransactionRule,
    action: 'submit' | 'approve' | 'disable' | 'reject'
  ) => {
    const body =
      action === 'reject'
        ? { rationale: window.prompt('Why is this rule being returned?') ?? '' }
        : undefined;
    if (action === 'reject' && !body?.rationale.trim()) return;
    try {
      await api.post(`/api/transaction-audit-rules/${rule.id}/${action}`, body);
      toast(
        `Transaction rule ${action === 'reject' ? 'returned' : action === 'approve' ? 'approved' : action === 'disable' ? 'disabled' : 'submitted'}.`,
        'success'
      );
      await load();
    } catch (cause) {
      setError(apiError(cause, `Could not ${action} the transaction rule.`));
    }
  };

  const edit = (rule: TransactionRule) => {
    setEditingId(rule.id);
    setDraft({
      name: rule.name,
      error_code: rule.error_code,
      record_type: rule.record_type ?? '',
      conditions: rule.conditions.map((condition) => ({
        ...condition,
        value: condition.value ?? '',
      })),
      match_mode: rule.match_mode,
      evaluation_scope: rule.evaluation_scope ?? 'Transaction',
      start_date: rule.start_date ?? '',
      end_date: rule.end_date ?? '',
      location_traits: rule.location_traits,
      execution_order: rule.execution_order,
      stop_processing: rule.stop_processing,
      owner: rule.owner ?? '',
    });
  };

  const runPreview = async () => {
    if (!previewRule) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.get(`/api/transaction-audit-rules/${previewRule.id}/preview`, {
        params: {
          date_from: previewFrom,
          date_to: previewTo,
          ...(previewStore ? { store_id: previewStore } : {}),
        },
      });
      setPreview(data);
    } catch (cause) {
      setError(apiError(cause, 'Could not preview this transaction rule.'));
    } finally {
      setBusy(false);
    }
  };

  const exportPreview = async () => {
    if (!previewRule || !preview?.would_trigger) return;
    setPreviewExporting(true);
    setError(null);
    try {
      const { data, headers } = await api.get(
        `/api/transaction-audit-rules/${previewRule.id}/preview`,
        {
          params: {
            date_from: previewFrom,
            date_to: previewTo,
            format: 'csv',
            ...(previewStore ? { store_id: previewStore } : {}),
          },
          responseType: 'blob',
        }
      );
      downloadBlob(`audit-rule-preview-${previewRule.id}.csv`, data);
      if (headers?.['x-preview-truncated'] === 'true') {
        toast('The preview export reached its 50,000 row limit.', 'error');
      }
    } catch (cause) {
      setError(apiError(cause, 'Could not export the rule preview.'));
    } finally {
      setPreviewExporting(false);
    }
  };

  const exportRuleBundle = () => {
    const exportedAt = new Date().toISOString();
    const bundle = {
      format: 'zetsa.transaction-audit-rules',
      version: 1,
      exported_at: exportedAt,
      rules: rules.map((rule) => ({ ...rule })),
    };
    downloadBlob(
      `transaction-audit-rules-${exportedAt.slice(0, 10)}.json`,
      new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json;charset=utf-8' })
    );
  };

  const previewRuleBundle = async (file?: File) => {
    if (!file) return;
    setImporting(true);
    setError(null);
    try {
      if (file.size > 2_000_000) throw new Error('Rule packages must be smaller than 2 MB.');
      const bundle = JSON.parse(await file.text()) as RulePackage;
      const { data } = await api.post<RulePackagePreview>(
        '/api/transaction-audit-rules/import/preview',
        bundle
      );
      setPackagePreview({ fileName: file.name, bundle, result: data });
    } catch (cause) {
      setPackagePreview(null);
      setError(
        cause instanceof SyntaxError
          ? 'The selected file is not valid JSON.'
          : cause instanceof Error && !('response' in cause)
            ? cause.message
            : apiError(cause, 'Could not preview this rule package.')
      );
    } finally {
      setImporting(false);
      if (packageInput.current) packageInput.current.value = '';
    }
  };

  const applyRuleBundle = async () => {
    if (!packagePreview?.result.valid || !packagePreview.result.preview_token) return;
    setImporting(true);
    setError(null);
    try {
      const { data } = await api.post('/api/transaction-audit-rules/import/apply', {
        ...packagePreview.bundle,
        preview_token: packagePreview.result.preview_token,
      });
      setPackagePreview(null);
      await load();
      toast(`Imported ${data.created} rule worksheet(s).`, 'success');
    } catch (cause) {
      setError(apiError(cause, 'Could not import this rule package. Preview it again and retry.'));
    } finally {
      setImporting(false);
    }
  };

  const previewColumns: Column<Preview['samples'][number]>[] = [
    { key: 'store', header: 'Store', primary: true, render: (row) => row.store_id },
    { key: 'date', header: 'Business date', render: (row) => row.business_date },
    { key: 'record', header: 'Record', render: (row) => row.record_type },
    { key: 'register', header: 'Register', render: (row) => row.register_id ?? '—' },
    { key: 'cashier', header: 'Cashier', render: (row) => row.cashier_id ?? '—' },
    {
      key: 'amount',
      header: 'Amount',
      numeric: true,
      render: (row) => (row.currency ? money(row.amount, row.currency) : '—'),
    },
  ];
  const canEdit = me?.role === 'finance' || me?.role === 'admin';
  const canTest = canEdit || me?.role === 'it';

  return (
    <div className="stack-lg">
      {error && (
        <Alert tone="error" onDismiss={() => setError(null)}>
          {error}
        </Alert>
      )}
      <Card title="Transaction rules">
        <p>
          Match canonical POS and e-commerce records against simple field conditions. Findings use
          the selected Sales Audit error code and enter the normal case and export-impact workflow.
          Rules run when eligible records pass through reconciliation.
        </p>
        <div className="cfg-actions">
          <span className="hint">
            Export the full rule set as a versioned JSON package, including conditions, scope,
            effective dates, revision, and approval state.
          </span>
          <Button variant="secondary" disabled={!rules.length} onClick={exportRuleBundle}>
            Download rule package
          </Button>
        </div>
        {canEdit && (
          <Field
            label="Import a rule package"
            hint="Choose a versioned JSON package. After preview, every imported rule is a disabled worksheet that must follow the normal approval workflow."
          >
            <input
              ref={packageInput}
              type="file"
              accept="application/json,.json"
              disabled={importing}
              onChange={(event) => void previewRuleBundle(event.target.files?.[0])}
            />
          </Field>
        )}
        {canEdit && (
          <div className="stack-md">
            <div className="profile-form">
              <input
                aria-label="Rule name"
                placeholder="Rule name"
                value={draft.name}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              />
              <select
                aria-label="Sales Audit error"
                value={draft.error_code}
                onChange={(event) => setDraft({ ...draft, error_code: event.target.value })}
              >
                <option value="">Choose an error code</option>
                {errorCodes.map((item) => (
                  <option key={item.code} value={item.code}>
                    {item.code} — {item.label}
                  </option>
                ))}
              </select>
              <select
                aria-label="Record type"
                value={draft.record_type}
                onChange={(event) => setDraft({ ...draft, record_type: event.target.value })}
              >
                <option value="">All POS and e-commerce records</option>
                {['Sale', 'Return', 'Order', 'Refund'].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
              <select
                aria-label="Evaluation scope"
                value={draft.evaluation_scope}
                onChange={(event) => {
                  const evaluation_scope = event.target.value as RuleDraft['evaluation_scope'];
                  setDraft({
                    ...draft,
                    evaluation_scope,
                    match_mode: evaluation_scope === 'StoreDayAbsence' ? 'Match' : draft.match_mode,
                  });
                }}
              >
                <option value="Transaction">Evaluate each transaction</option>
                <option value="StoreDayAbsence">Raise when Store Day has no matching record</option>
              </select>
              <select
                aria-label="Condition result that triggers the error"
                value={draft.match_mode}
                disabled={draft.evaluation_scope === 'StoreDayAbsence'}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    match_mode: event.target.value as TransactionRule['match_mode'],
                  })
                }
              >
                <option value="Match">When all conditions match</option>
                {draft.evaluation_scope === 'Transaction' && (
                  <option value="No Match">When conditions do not match</option>
                )}
              </select>
            </div>
            <div className="stack-sm" aria-label="Rule conditions">
              <p className="hint">
                {draft.evaluation_scope === 'StoreDayAbsence'
                  ? 'All conditions are combined with AND. The rule raises once for an open Store Day when no POS or e-commerce record matches.'
                  : 'All conditions are combined with AND. Use empty checks for missing attributes.'}
              </p>
              {draft.conditions.map((condition, index) => {
                const numeric = fields.find((field) => field.key === condition.field)?.numeric;
                const operators = numeric ? numberOperators : textOperators;
                return (
                  <div className="profile-form" key={`${index}:${condition.field}`}>
                    <select
                      aria-label={`Condition ${index + 1} field`}
                      value={condition.field}
                      onChange={(event) => {
                        const next = [...draft.conditions];
                        const field = event.target.value as FieldName;
                        next[index] = {
                          field,
                          operator: fields.find((item) => item.key === field)?.numeric
                            ? 'gt'
                            : 'eq',
                          value: '',
                        };
                        setDraft({ ...draft, conditions: next });
                      }}
                    >
                      {fields.map((field) => (
                        <option key={field.key} value={field.key}>
                          {field.label}
                        </option>
                      ))}
                    </select>
                    <select
                      aria-label={`Condition ${index + 1} operator`}
                      value={condition.operator}
                      onChange={(event) => {
                        const next = [...draft.conditions];
                        next[index] = {
                          ...condition,
                          operator: event.target.value as Operator,
                          value: ['is_empty', 'not_empty'].includes(event.target.value)
                            ? ''
                            : condition.value,
                        };
                        setDraft({ ...draft, conditions: next });
                      }}
                    >
                      {operators.map((operator) => (
                        <option key={operator} value={operator}>
                          {operatorLabels[operator]}
                        </option>
                      ))}
                    </select>
                    {!['is_empty', 'not_empty'].includes(condition.operator) && (
                      <input
                        aria-label={`Condition ${index + 1} value`}
                        placeholder="Value"
                        type={numeric ? 'number' : 'text'}
                        step={condition.field === 'signed_amount' ? '0.01' : 'any'}
                        value={condition.value}
                        onChange={(event) => {
                          const next = [...draft.conditions];
                          next[index] = { ...condition, value: event.target.value };
                          setDraft({ ...draft, conditions: next });
                        }}
                      />
                    )}
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={draft.conditions.length === 1}
                      onClick={() =>
                        setDraft({
                          ...draft,
                          conditions: draft.conditions.filter((_, i) => i !== index),
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
                disabled={draft.conditions.length >= 20}
                onClick={() =>
                  setDraft({ ...draft, conditions: [...draft.conditions, blankCondition()] })
                }
              >
                Add condition
              </Button>
            </div>
            <div className="profile-form">
              <Field label="Effective from">
                <input
                  type="date"
                  value={draft.start_date}
                  onChange={(event) => setDraft({ ...draft, start_date: event.target.value })}
                />
              </Field>
              <Field label="Effective through">
                <input
                  type="date"
                  min={draft.start_date || undefined}
                  value={draft.end_date}
                  onChange={(event) => setDraft({ ...draft, end_date: event.target.value })}
                />
              </Field>
              <Field label="Execution order" hint="Lower values run first; ties run by name.">
                <input
                  type="number"
                  min="0"
                  max="100000"
                  value={draft.execution_order}
                  onChange={(event) =>
                    setDraft({ ...draft, execution_order: Number(event.target.value) })
                  }
                />
              </Field>
              <Field label="Owner">
                <input
                  placeholder="Optional owner"
                  value={draft.owner}
                  onChange={(event) => setDraft({ ...draft, owner: event.target.value })}
                />
              </Field>
              <Field
                label="Stop later transaction rules on breach"
                hint="Stops later configured transaction rules for this Store Day."
              >
                <input
                  type="checkbox"
                  checked={draft.stop_processing}
                  onChange={(event) =>
                    setDraft({ ...draft, stop_processing: event.target.checked })
                  }
                />
              </Field>
            </div>
            {locationTraitOptions.length > 0 && (
              <Field
                label="Store location traits"
                hint="Any selected trait includes the store; leave empty for all stores."
              >
                <div className="cluster" role="group" aria-label="Store location traits">
                  {locationTraitOptions.map((trait) => (
                    <label className="inline-actions" key={trait}>
                      <input
                        type="checkbox"
                        checked={draft.location_traits.includes(trait)}
                        onChange={(event) =>
                          setDraft({
                            ...draft,
                            location_traits: event.target.checked
                              ? [...draft.location_traits, trait]
                              : draft.location_traits.filter((value) => value !== trait),
                          })
                        }
                      />
                      {trait}
                    </label>
                  ))}
                </div>
              </Field>
            )}
            <div className="cfg-actions">
              <Button
                disabled={
                  busy ||
                  !draft.name.trim() ||
                  !draft.error_code ||
                  draft.conditions.some(
                    (condition) =>
                      !['is_empty', 'not_empty'].includes(condition.operator) &&
                      !condition.value.trim()
                  )
                }
                onClick={() => void saveDraft()}
              >
                {editingId ? 'Save worksheet' : 'Create worksheet'}
              </Button>
              {editingId && (
                <Button
                  variant="secondary"
                  onClick={() => {
                    setEditingId(null);
                    setDraft(blankRule());
                  }}
                >
                  Cancel edit
                </Button>
              )}
            </div>
          </div>
        )}
        <ControlsTable
          headers={[
            'Rule',
            'Record',
            'Scope',
            'Error',
            'Conditions',
            'Order',
            'Status · revision',
            'Workflow',
          ]}
          rows={rules.map((rule) => [
            rule.name,
            rule.record_type ?? 'All POS / e-commerce',
            rule.evaluation_scope === 'StoreDayAbsence' ? 'Store Day absence' : 'Each transaction',
            rule.error_code,
            `${rule.evaluation_scope === 'StoreDayAbsence' ? 'No matching record' : rule.match_mode === 'Match' ? 'Match' : 'No match'} · ${rule.conditions.length} condition(s)`,
            rule.execution_order,
            `${rule.status} · ${rule.revision}${rule.approved_by ? ` · approved by ${rule.approved_by}` : ''}`,
            <span key={rule.id} className="inline-actions">
              {rule.status === 'Worksheet' && canTest && (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    setPreviewRule(rule);
                    setPreview(null);
                  }}
                >
                  Preview
                </Button>
              )}
              {rule.status === 'Worksheet' && canEdit && (
                <>
                  <Button size="sm" variant="secondary" onClick={() => edit(rule)}>
                    Edit
                  </Button>
                  <Button size="sm" onClick={() => void act(rule, 'submit')}>
                    Submit
                  </Button>
                </>
              )}
              {rule.status === 'Submitted' && canEdit && (
                <>
                  <Button size="sm" onClick={() => void act(rule, 'approve')}>
                    Approve
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => void act(rule, 'reject')}>
                    Return
                  </Button>
                </>
              )}
              {rule.status === 'Active' && canEdit && (
                <Button size="sm" variant="secondary" onClick={() => void act(rule, 'disable')}>
                  Disable
                </Button>
              )}
            </span>,
          ])}
        />
      </Card>
      {packagePreview && (
        <Card title={`Import preview · ${packagePreview.fileName}`}>
          <Alert tone={packagePreview.result.valid ? 'warning' : 'error'}>
            {packagePreview.result.valid
              ? `${packagePreview.result.counts.worksheets} new worksheet(s) will be created. Imported rules remain disabled and require separate review and approval.`
              : 'This package contains errors. Correct them in the source package and preview again.'}
          </Alert>
          {packagePreview.result.errors.length > 0 && (
            <ul className="validation-errors">
              {packagePreview.result.errors.map((item, index) => (
                <li key={`${item.row}:${index}`}>
                  Rule {item.row}: {item.message}
                </li>
              ))}
            </ul>
          )}
          <DataTable
            columns={[
              { key: 'row', header: 'Package row', render: (row) => row.row },
              { key: 'name', header: 'Rule', primary: true, render: (row) => row.name },
              { key: 'error', header: 'Error code', render: (row) => row.error_code },
              {
                key: 'conditions',
                header: 'Conditions',
                numeric: true,
                render: (row) => formatNumber(row.condition_count),
              },
              { key: 'action', header: 'Import action', render: (row) => row.action },
            ]}
            rows={packagePreview.result.rows}
            rowKey={(row) => `${row.row}:${row.name}`}
            emptyTitle="No valid rule definitions"
          />
          <div className="cfg-actions">
            <Button
              variant="primary"
              disabled={!packagePreview.result.valid || importing || busy}
              onClick={() => void applyRuleBundle()}
            >
              {importing ? 'Importing…' : 'Create worksheets'}
            </Button>
            <Button
              variant="secondary"
              disabled={importing}
              onClick={() => setPackagePreview(null)}
            >
              Cancel import
            </Button>
          </div>
        </Card>
      )}
      {previewRule && (
        <Card title={`Preview · ${previewRule.name}`}>
          <p className="hint">
            {previewRule.evaluation_scope === 'StoreDayAbsence'
              ? 'Read-only preview of open Store Days where no eligible POS or e-commerce record matches.'
              : 'Read-only preview. It does not create cases or change transaction status. A rule runs when eligible transactions pass through reconciliation.'}
          </p>
          <div className="cfg-form">
            <StoreSelect
              value={previewStore}
              emptyLabel="All stores"
              onChange={(value) => {
                setPreviewStore(value);
                setPreview(null);
              }}
            />
            <Field label="From business date">
              <input
                type="date"
                value={previewFrom}
                onChange={(event) => {
                  setPreviewFrom(event.target.value);
                  setPreview(null);
                }}
              />
            </Field>
            <Field label="Through business date">
              <input
                type="date"
                value={previewTo}
                onChange={(event) => {
                  setPreviewTo(event.target.value);
                  setPreview(null);
                }}
              />
            </Field>
            <Button
              disabled={
                busy ||
                !previewFrom ||
                !previewTo ||
                previewFrom > previewTo ||
                (previewRule.location_traits.length > 0 && !previewStore)
              }
              onClick={() => void runPreview()}
            >
              {busy ? 'Previewing…' : 'Run preview'}
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setPreviewRule(null);
                setPreview(null);
              }}
            >
              Close
            </Button>
          </div>
          {preview && (
            <>
              <MetricGrid>
                <MetricTile
                  label={
                    preview.evaluation_scope === 'StoreDayAbsence'
                      ? 'Store Days evaluated'
                      : 'Records evaluated'
                  }
                  value={formatNumber(preview.evaluated)}
                />
                <MetricTile label="Would raise error" value={formatNumber(preview.would_trigger)} />
                <MetricTile label="Error code" value={preview.error_code} />
              </MetricGrid>
              <div className="row-actions" style={{ marginTop: '1rem' }}>
                <Button
                  variant="secondary"
                  disabled={!preview.would_trigger || previewExporting || busy}
                  onClick={() => void exportPreview()}
                >
                  {previewExporting ? 'Preparing CSV…' : 'Download all matching rows'}
                </Button>
                <span className="hint">
                  Uses the same store and date filters as this read-only preview. Exports up to
                  50,000 matching rows.
                </span>
              </div>
              {preview.would_trigger > preview.sample_limit && (
                <p className="hint">Showing the 50 most recent matching records.</p>
              )}
              <DataTable
                columns={previewColumns}
                rows={preview.samples}
                rowKey={(row) =>
                  row.transaction_id ?? `${row.store_id}:${row.business_date}:absence`
                }
                emptyTitle={
                  preview.evaluation_scope === 'StoreDayAbsence'
                    ? 'No Store Days lack a matching record'
                    : 'No records match these conditions'
                }
                emptyHint="Adjust the rule or date range and preview again."
              />
            </>
          )}
        </Card>
      )}
    </div>
  );
}
