'use client';
import { formatNumber } from '@/lib/preferences';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, money } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { useToast } from '@/components/Feedback';
import { StoreSelect } from '@/components/StoreSelect';
import ControlsTable from '@/components/settings/ControlsTable';
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  Field,
  MetricGrid,
  MetricTile,
  Tabs,
} from '@/components/ui';
import type { Column } from '@/components/ui';
import { SettingsSection } from '@/components/settings/SettingsSection';
import { AuditRulePackages } from '@/components/settings/AuditRulePackages';
import { TransactionRules } from '@/components/settings/TransactionRules';
import { TotalDefinitions, type TotalRevision } from '@/components/settings/TotalDefinitions';
import { ErrorDefinitionPackages } from '@/components/settings/ErrorDefinitionPackages';
import { useMe } from '@/components/MeProvider';
type Rule = {
  id: string;
  name: string;
  total_name: string;
  start_date: string | null;
  end_date: string | null;
  location_traits: string[];
  execution_order: number;
  stop_processing: boolean;
  balance_level: 'Store' | 'Register' | 'Cashier';
  comparison: 'ABS_GT' | 'GT' | 'GTE' | 'LT' | 'LTE' | 'EQ' | 'NE';
  threshold: string;
  threshold_currency: string | null;
  severity: string;
  owner: string | null;
  status: 'Worksheet' | 'Submitted' | 'Active' | 'Disabled';
  revision: number;
  submitted_by: string | null;
  approved_by: string | null;
};
type RulePreview = {
  rule_name: string;
  total_name: string;
  comparison: Rule['comparison'];
  threshold: string;
  threshold_currency: string | null;
  date_from: string;
  date_to: string;
  effective_start_date: string | null;
  effective_end_date: string | null;
  location_traits: string[];
  execution_order: number;
  stop_processing: boolean;
  balance_level: 'Store' | 'Register' | 'Cashier';
  evaluated: number;
  would_breach: number;
  sample_limit: number;
  samples: {
    store_id: string;
    business_date: string;
    currency: string;
    dimension: string | null;
    observed: string;
    would_breach: boolean;
  }[];
};
type GL = {
  id: string;
  transaction_type: string;
  tender_type: string | null;
  debit_account: string;
  credit_account: string;
  active: boolean;
};
type GLPackage = {
  format: 'zetsa.gl-cross-references';
  version: 1;
  mappings: Omit<GL, 'id' | 'updated_by' | 'updated_at'>[];
};
type GLPackageResult = {
  valid: boolean;
  preview_token: string | null;
  errors: { row: number; message: string }[];
  counts: { create: number; update: number; unchanged: number };
  mappings: (Omit<GL, 'id' | 'updated_by' | 'updated_at'> & {
    row: number;
    action: string;
  })[];
};
type Retention = {
  id: string;
  dataset: string;
  retain_days: number;
  archive_before_purge: boolean;
};
type RetentionRun = {
  id: string;
  dataset: string;
  eligible_records: number;
  archived_records: number;
  purged_records: number;
  status: string;
  created_at: string;
};
const glApiError = (error: any, fallback: string) => {
  const detail = error?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg ?? JSON.stringify(item)).join('; ');
  }
  return fallback;
};
const tabs = ['Totals & rules', 'Transaction rules', 'GL mapping', 'Error codes'] as const;
type Tab = (typeof tabs)[number];
const comparisonLabels: Record<Rule['comparison'], string> = {
  ABS_GT: 'absolute value >',
  GT: '>',
  GTE: '≥',
  LT: '<',
  LTE: '≤',
  EQ: '=',
  NE: '≠',
};
export default function ControlsPage() {
  const toast = useToast();
  const { me } = useMe();
  const [tab, setTab] = useState<Tab>('Totals & rules');
  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get('tab');
    if (requested && tabs.includes(requested as Tab)) setTab(requested as Tab);
  }, []);
  const [ruleDraft, setRuleDraft] = useState({
    name: '',
    total_name: '',
    start_date: '',
    end_date: '',
    location_traits: [] as string[],
    execution_order: 100,
    stop_processing: false,
    balance_level: 'Store' as Rule['balance_level'],
    comparison: 'ABS_GT' as Rule['comparison'],
    threshold: '0.00',
    threshold_currency: 'USD',
    severity: 'Medium',
    owner: '',
  });
  const [editingRuleId, setEditingRuleId] = useState<string | null>(null);
  const [previewRule, setPreviewRule] = useState<Rule | null>(null);
  const [previewStore, setPreviewStore] = useState('');
  const [previewFrom, setPreviewFrom] = useState(() => {
    const start = new Date();
    start.setDate(start.getDate() - 29);
    return start.toISOString().slice(0, 10);
  });
  const [previewTo, setPreviewTo] = useState(() => new Date().toISOString().slice(0, 10));
  const [previewData, setPreviewData] = useState<RulePreview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [locationTraitOptions, setLocationTraitOptions] = useState<string[]>([]);
  const [totals, setTotals] = useState<TotalRevision[]>([]),
    [rules, setRules] = useState<Rule[]>([]),
    [maps, setMaps] = useState<GL[]>([]),
    [error, setError] = useState<string | null>(null),
    [notice, setNotice] = useState<string | null>(null);
  const [editingGlId, setEditingGlId] = useState<string | null>(null);
  const [glReason, setGlReason] = useState('');
  const [glPackagePreview, setGlPackagePreview] = useState<{
    fileName: string;
    bundle: GLPackage;
    result: GLPackageResult;
  } | null>(null);
  const [glPackageReason, setGlPackageReason] = useState('');
  const [glPackageBusy, setGlPackageBusy] = useState(false);
  const glPackageInput = useRef<HTMLInputElement>(null);
  const [gl, setGl] = useState({
      transaction_type: 'Sale',
      tender_type: '',
      debit_account: '',
      credit_account: '',
      active: true,
    }),
    [journalStore, setJournalStore] = useState(''),
    [journalDate, setJournalDate] = useState(new Date().toISOString().slice(0, 10)),
    [journalPage, setJournalPage] = useState(1),
    [journal, setJournal] = useState<{
      lines: {
        transaction_id: string;
        debit_account: string;
        credit_account: string;
        amount: string;
        currency: string;
      }[];
      unmapped_transaction_ids: string[];
      total_transactions: number;
      page: number;
      page_size: number;
      pages: number;
    } | null>(null);
  const loadJournal = async (page = 1) => {
    try {
      const { data } = await api.get('/api/gl/journal-preview', {
        params: { store_id: journalStore, business_date: journalDate, page, page_size: 100 },
      });
      setJournal(data);
      setJournalPage(page);
      setError(null);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not generate journal preview.');
    }
  };
  const load = useCallback(async () => {
    try {
      const [b, c, d] = await Promise.all([
        api.get('/api/total-definitions'),
        api.get('/api/audit-rules'),
        api.get('/api/gl/cross-references'),
      ]);
      setTotals(b.data);
      setRules(c.data);
      setMaps(d.data);
      setError(null);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Unable to load control configuration.');
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (!me || !['finance', 'it', 'admin'].includes(me.role)) return;
    api
      .get('/api/foundation/location-traits')
      .then(({ data }) => setLocationTraitOptions(data.items))
      .catch(() => setLocationTraitOptions([]));
  }, [me]);
  const save = async (action: () => Promise<unknown>, message: string) => {
    try {
      await action();
      await load();
      toast(message, 'success');
      return true;
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Unable to save configuration.');
      return false;
    }
  };
  const saveGL = async () => {
    const payload = { ...gl, tender_type: gl.tender_type || null };
    const saved = await save(
      () =>
        editingGlId
          ? api.put(`/api/gl/cross-references/${editingGlId}`, {
              ...payload,
              reason: glReason,
            })
          : api.post('/api/gl/cross-references', { ...payload, active: true }),
      editingGlId ? 'GL mapping updated and audited.' : 'GL mapping saved and audited.'
    );
    if (saved) {
      setEditingGlId(null);
      setGlReason('');
      setGl({
        transaction_type: 'Sale',
        tender_type: '',
        debit_account: '',
        credit_account: '',
        active: true,
      });
    }
  };
  const editGL = (mapping: GL) => {
    setEditingGlId(mapping.id);
    setGl({ ...mapping, tender_type: mapping.tender_type ?? '' });
    setGlReason('');
    setError(null);
  };
  const downloadGLPackage = async () => {
    try {
      const { data } = await api.get<GLPackage>('/api/gl/cross-references/package');
      downloadBlob(
        `gl-cross-references-${new Date().toISOString().slice(0, 10)}.json`,
        new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' })
      );
      toast('GL cross-reference snapshot downloaded.', 'success');
    } catch (e: any) {
      setError(glApiError(e, 'Could not download GL mappings.'));
    }
  };
  const previewGLPackage = async (file?: File) => {
    if (!file) return;
    setGlPackageBusy(true);
    setError(null);
    setGlPackagePreview(null);
    try {
      if (file.size > 2_000_000) throw new Error('GL snapshots must be smaller than 2 MB.');
      const bundle = JSON.parse(await file.text()) as GLPackage;
      const { data } = await api.post<GLPackageResult>(
        '/api/gl/cross-references/package/preview',
        bundle
      );
      setGlPackagePreview({ fileName: file.name, bundle, result: data });
      setGlPackageReason('');
    } catch (e: any) {
      setError(
        e instanceof SyntaxError
          ? 'The selected GL snapshot is not valid JSON.'
          : glApiError(e, e.message ?? 'Could not preview the GL snapshot.')
      );
    } finally {
      setGlPackageBusy(false);
      if (glPackageInput.current) glPackageInput.current.value = '';
    }
  };
  const applyGLPackage = async () => {
    if (!glPackagePreview?.result.valid || !glPackagePreview.result.preview_token) return;
    setGlPackageBusy(true);
    setError(null);
    try {
      const { data } = await api.post('/api/gl/cross-references/package/apply', {
        ...glPackagePreview.bundle,
        preview_token: glPackagePreview.result.preview_token,
        reason: glPackageReason,
      });
      setGlPackagePreview(null);
      setGlPackageReason('');
      await load();
      toast(
        `GL snapshot applied: ${data.created} created, ${data.updated} updated, ${data.unchanged} unchanged.`,
        'success'
      );
    } catch (e: any) {
      setError(glApiError(e, 'Could not apply the GL snapshot. Preview it again.'));
    } finally {
      setGlPackageBusy(false);
    }
  };
  const createRule = () =>
    save(
      () =>
        editingRuleId
          ? api.put(`/api/audit-rules/${editingRuleId}`, {
              ...ruleDraft,
              start_date: ruleDraft.start_date || null,
              end_date: ruleDraft.end_date || null,
              threshold: ruleDraft.threshold,
              threshold_currency: ruleDraft.threshold_currency || null,
              owner: ruleDraft.owner || null,
            })
          : api.post('/api/audit-rules', {
              ...ruleDraft,
              start_date: ruleDraft.start_date || null,
              end_date: ruleDraft.end_date || null,
              threshold: ruleDraft.threshold,
              threshold_currency: ruleDraft.threshold_currency || null,
              owner: ruleDraft.owner || null,
            }),
      editingRuleId ? 'Audit rule worksheet updated.' : 'Audit rule worksheet created.'
    ).then((created) => {
      if (created) {
        setRuleDraft({
          ...ruleDraft,
          name: '',
          start_date: '',
          end_date: '',
          location_traits: [],
          execution_order: 100,
          stop_processing: false,
          balance_level: 'Store',
          threshold: '0.00',
          owner: '',
        });
        setEditingRuleId(null);
      }
    });
  const editRule = (rule: Rule) => {
    setEditingRuleId(rule.id);
    setRuleDraft({
      name: rule.name,
      total_name: rule.total_name,
      start_date: rule.start_date ?? '',
      end_date: rule.end_date ?? '',
      location_traits: rule.location_traits ?? [],
      execution_order: rule.execution_order,
      stop_processing: rule.stop_processing,
      balance_level: rule.balance_level,
      comparison: rule.comparison,
      threshold: rule.threshold,
      threshold_currency: rule.threshold_currency ?? '',
      severity: rule.severity,
      owner: rule.owner ?? '',
    });
  };
  const ruleAction = (rule: Rule, action: 'submit' | 'approve' | 'disable' | 'reject') =>
    save(
      () =>
        action === 'reject'
          ? api.post(`/api/audit-rules/${rule.id}/reject`, {
              rationale: window.prompt('Why is this rule being returned?') ?? '',
            })
          : api.post(`/api/audit-rules/${rule.id}/${action}`),
      `Rule ${action === 'submit' ? 'submitted' : action === 'approve' ? 'approved' : action === 'disable' ? 'disabled' : 'returned to worksheet'}.`
    );
  const runRulePreview = async () => {
    if (!previewRule) return;
    setPreviewBusy(true);
    setError(null);
    try {
      const { data } = await api.get(`/api/audit-rules/${previewRule.id}/preview`, {
        params: {
          date_from: previewFrom,
          date_to: previewTo,
          ...(previewStore ? { store_id: previewStore } : {}),
        },
      });
      setPreviewData(data);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not test this worksheet rule.');
    } finally {
      setPreviewBusy(false);
    }
  };
  const previewColumns: Column<RulePreview['samples'][number]>[] = [
    {
      key: 'store',
      header: 'Store',
      primary: true,
      render: (row) => row.store_id,
      csvValue: (row) => row.store_id,
    },
    {
      key: 'date',
      header: 'Business date',
      render: (row) => row.business_date,
      csvValue: (row) => row.business_date,
    },
    {
      key: 'dimension',
      header: previewRule?.balance_level ?? 'Balance level',
      render: (row) => row.dimension ?? 'Whole store',
      csvValue: (row) => row.dimension ?? 'Whole store',
    },
    {
      key: 'observed',
      header: 'Observed total',
      numeric: true,
      render: (row) =>
        previewRule?.threshold_currency
          ? money(row.observed, row.currency)
          : `${row.observed} count`,
      csvValue: (row) =>
        previewRule?.threshold_currency ? `${row.observed} ${row.currency}` : `${row.observed} count`,
    },
    {
      key: 'outcome',
      header: 'Rule result',
      render: (row) => (
        <Badge variant={row.would_breach ? 'error' : 'success'} size="sm">
          {row.would_breach ? 'Would trigger' : 'Within threshold'}
        </Badge>
      ),
      csvValue: (row) => (row.would_breach ? 'Would trigger' : 'Within threshold'),
    },
  ];
  const isConfiguredTarget = ruleDraft.total_name.startsWith('Configured: ');
  const configuredTargetName = ruleDraft.total_name.replace(/^Configured: /, '');
  const configuredTarget = isConfiguredTarget
    ? totals.find(
        (total) => total.name === configuredTargetName && total.status === 'Active' && total.active
      )
    : undefined;
  const supportedBalanceLevels: Rule['balance_level'][] = configuredTarget
    ? ['Store', 'Register', 'Cashier'].includes(configuredTarget.level)
      ? [configuredTarget.level as Rule['balance_level']]
      : []
    : configuredTargetName === 'POS Net'
      ? ['Store', 'Register']
      : ['Store'];
  return (
    <SettingsSection
      title="Totals, rules & GL mapping"
      description="Governed controls used when Store Days are recalculated. Stores, products and other reference data are under Store Day Audit → Foundation Data."
    >
      <Tabs
        value={tab}
        onChange={(id) => setTab(id as Tab)}
        items={tabs.map((t) => ({ id: t, label: t }))}
      />
      {error && (
        <Alert tone="error" onDismiss={() => setError(null)}>
          {error}
        </Alert>
      )}
      {notice && (
        <Alert tone="info" onDismiss={() => setNotice(null)}>
          {notice}
        </Alert>
      )}
      {tab === 'Totals & rules' && (
        <>
          <TotalDefinitions revisions={totals} onChanged={load} />
          <Card title="Threshold rules">
            <p>
              Draft a total threshold, submit it for independent approval, then activate it for
              future re-totals. An approved rule takes effect after store-day totals are refreshed.
            </p>
            {(me?.role === 'finance' || me?.role === 'admin') && (
              <div className="profile-form">
                <input
                  aria-label="Rule name"
                  placeholder="Rule name"
                  value={ruleDraft.name}
                  onChange={(e) => setRuleDraft({ ...ruleDraft, name: e.target.value })}
                />
                <input
                  aria-label="Target total"
                  placeholder="Target total name"
                  list="audit-rule-total-names"
                  value={ruleDraft.total_name}
                  onChange={(e) => {
                    const value = e.target.value;
                    const targetName = value.replace(/^Configured: /, '');
                    const target = value.startsWith('Configured: ')
                      ? totals.find(
                          (total) =>
                            total.name === targetName && total.status === 'Active' && total.active
                        )
                      : undefined;
                    const level =
                      target && ['Store', 'Register', 'Cashier'].includes(target.level)
                        ? (target.level as Rule['balance_level'])
                        : 'Store';
                    setRuleDraft({ ...ruleDraft, total_name: value, balance_level: level });
                  }}
                />
                <datalist id="audit-rule-total-names">
                  {[
                    'POS Net',
                    'OVRSHT_S',
                    'SALES_POS',
                    'RETURNS_POS',
                    'SALES_ECOM',
                    'RETURNS_ECOM',
                    'TAX_POS',
                    'TAX_ECOM',
                    'TEND_CASH',
                    'TEND_CARD',
                    'TEND_VOUCHER',
                    'TEND_GIFTCARD',
                    'TEND_STORECREDIT',
                  ].map((name) => (
                    <option key={name} value={name} />
                  ))}
                  {[
                    ...new Set(
                      totals.filter((t) => t.status === 'Active' && t.active).map((t) => t.name)
                    ),
                  ].map((name) => (
                    <option key={name} value={name} />
                  ))}
                  {totals
                    .filter(
                      (t) =>
                        t.status === 'Active' &&
                        t.active &&
                        ['Store', 'Register', 'Cashier'].includes(t.level)
                    )
                    .map((t) => (
                      <option key={`configured:${t.id}`} value={`Configured: ${t.name}`} />
                    ))}
                </datalist>
                <select
                  aria-label="Threshold comparison"
                  value={ruleDraft.comparison}
                  onChange={(e) =>
                    setRuleDraft({ ...ruleDraft, comparison: e.target.value as Rule['comparison'] })
                  }
                >
                  {(Object.keys(comparisonLabels) as Rule['comparison'][]).map((operator) => (
                    <option key={operator} value={operator}>
                      {comparisonLabels[operator]}
                    </option>
                  ))}
                </select>
                <small className="hint">
                  Signed operators compare the total as calculated. Absolute value keeps the
                  original two-direction threshold behavior.
                </small>
                <Field label="Effective from" hint="Leave blank to include earlier business dates.">
                  <input
                    type="date"
                    value={ruleDraft.start_date}
                    onChange={(e) => setRuleDraft({ ...ruleDraft, start_date: e.target.value })}
                  />
                </Field>
                <Field label="Effective through" hint="Leave blank for no end date.">
                  <input
                    type="date"
                    min={ruleDraft.start_date || undefined}
                    value={ruleDraft.end_date}
                    onChange={(e) => setRuleDraft({ ...ruleDraft, end_date: e.target.value })}
                  />
                </Field>
                <Field
                  label="Apply to location traits"
                  hint="A rule applies when a store has any selected trait. Leave empty to apply to all stores."
                >
                  <div className="cluster" role="group" aria-label="Location traits">
                    {locationTraitOptions.length ? (
                      locationTraitOptions.map((trait) => (
                        <label key={trait} className="inline-actions">
                          <input
                            type="checkbox"
                            checked={ruleDraft.location_traits.includes(trait)}
                            onChange={(event) =>
                              setRuleDraft({
                                ...ruleDraft,
                                location_traits: event.target.checked
                                  ? [...ruleDraft.location_traits, trait]
                                  : ruleDraft.location_traits.filter((value) => value !== trait),
                              })
                            }
                          />
                          {trait}
                        </label>
                      ))
                    ) : (
                      <span className="hint">No active store location traits are configured.</span>
                    )}
                  </div>
                </Field>
                <Field
                  label="Balance level"
                  hint="Rules run once for each total at this level. Register and Cashier require a total defined at the same level."
                >
                  <select
                    value={ruleDraft.balance_level}
                    onChange={(event) =>
                      setRuleDraft({
                        ...ruleDraft,
                        balance_level: event.target.value as Rule['balance_level'],
                      })
                    }
                  >
                    {(['Store', 'Register', 'Cashier'] as const).map((level) => (
                      <option
                        key={level}
                        value={level}
                        disabled={!supportedBalanceLevels.includes(level)}
                      >
                        {level}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field
                  label="Execution order"
                  hint="Lower numbers run first. Rules with the same order run by name."
                >
                  <input
                    type="number"
                    min="0"
                    max="100000"
                    step="1"
                    value={ruleDraft.execution_order}
                    onChange={(e) =>
                      setRuleDraft({ ...ruleDraft, execution_order: Number(e.target.value) })
                    }
                  />
                </Field>
                <Field
                  label="Stop later threshold rules on breach"
                  hint="When this rule triggers, later configured threshold rules will not run for the Store Day."
                >
                  <input
                    type="checkbox"
                    checked={ruleDraft.stop_processing}
                    onChange={(e) =>
                      setRuleDraft({ ...ruleDraft, stop_processing: e.target.checked })
                    }
                  />
                </Field>
                <input
                  aria-label="Threshold amount"
                  type="number"
                  min="0"
                  step="0.01"
                  value={ruleDraft.threshold}
                  onChange={(e) => setRuleDraft({ ...ruleDraft, threshold: e.target.value })}
                />
                <input
                  aria-label="Currency"
                  maxLength={3}
                  placeholder="Currency or blank for Count"
                  value={ruleDraft.threshold_currency}
                  onChange={(e) =>
                    setRuleDraft({ ...ruleDraft, threshold_currency: e.target.value.toUpperCase() })
                  }
                />
                <select
                  aria-label="Severity"
                  value={ruleDraft.severity}
                  onChange={(e) => setRuleDraft({ ...ruleDraft, severity: e.target.value })}
                >
                  {['Low', 'Medium', 'High', 'Critical'].map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
                <input
                  aria-label="Rule owner"
                  placeholder="Owner (optional)"
                  value={ruleDraft.owner}
                  onChange={(e) => setRuleDraft({ ...ruleDraft, owner: e.target.value })}
                />
                <Button
                  onClick={createRule}
                  disabled={!ruleDraft.name.trim() || !ruleDraft.total_name.trim()}
                >
                  {editingRuleId ? 'Save worksheet changes' : 'Create worksheet'}
                </Button>
                {editingRuleId && (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setEditingRuleId(null);
                      setRuleDraft({
                        name: '',
                        total_name: '',
                        start_date: '',
                        end_date: '',
                        location_traits: [],
                        execution_order: 100,
                        stop_processing: false,
                        balance_level: 'Store',
                        comparison: 'ABS_GT',
                        threshold: '0.00',
                        threshold_currency: 'USD',
                        severity: 'Medium',
                        owner: '',
                      });
                    }}
                  >
                    Cancel edit
                  </Button>
                )}
              </div>
            )}
            <ControlsTable
              headers={[
                'Rule',
                'Target',
                'Threshold',
                'Run order',
                'Balance level',
                'Effective dates',
                'Locations',
                'Stop on breach',
                'Severity',
                'Owner',
                'Status · revision',
                'Workflow',
              ]}
              rows={rules.map((x) => [
                x.name,
                x.total_name,
                `${comparisonLabels[x.comparison]} ${x.threshold_currency ? money(x.threshold, x.threshold_currency) : `${x.threshold} (count)`}`,
                x.execution_order,
                x.balance_level,
                `${x.start_date ?? 'Any date'} – ${x.end_date ?? 'Ongoing'}`,
                x.location_traits.length ? x.location_traits.join(', ') : 'All stores',
                x.stop_processing ? 'Yes' : 'No',
                x.severity,
                x.owner || 'Unassigned',
                `${x.status} · ${x.revision}${x.approved_by ? ` · approved by ${x.approved_by}` : ''}`,
                <span key={x.id} className="inline-actions">
                  {x.status === 'Worksheet' &&
                    (me?.role === 'finance' || me?.role === 'it' || me?.role === 'admin') && (
                      <>
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => {
                            setPreviewRule(x);
                            setPreviewData(null);
                          }}
                        >
                          Test
                        </Button>
                        {(me?.role === 'finance' || me?.role === 'admin') && (
                          <>
                            <Button size="sm" variant="secondary" onClick={() => editRule(x)}>
                              Edit
                            </Button>
                            <Button size="sm" onClick={() => ruleAction(x, 'submit')}>
                              Submit
                            </Button>
                          </>
                        )}
                      </>
                    )}
                  {x.status === 'Submitted' && (me?.role === 'finance' || me?.role === 'admin') && (
                    <>
                      <Button size="sm" onClick={() => ruleAction(x, 'approve')}>
                        Approve
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => ruleAction(x, 'reject')}>
                        Return
                      </Button>
                    </>
                  )}
                  {x.status === 'Active' && (me?.role === 'finance' || me?.role === 'admin') && (
                    <Button size="sm" variant="secondary" onClick={() => ruleAction(x, 'disable')}>
                      Disable
                    </Button>
                  )}
                </span>,
              ])}
            />
            <AuditRulePackages onApplied={load} />
          </Card>
          {previewRule && (
            <Card title={`Test worksheet · ${previewRule.name}`}>
              <p>
                Compare current, non-stale Store Day totals with this rule. The preview is read-only
                and does not create cases or change totals.
              </p>
              <p className="hint">
                Effective window: {previewRule.start_date ?? 'Any date'} through{' '}
                {previewRule.end_date ?? 'Ongoing'}.
              </p>
              {previewRule.location_traits.length > 0 && (
                <p className="hint">
                  This rule applies to {previewRule.location_traits.join(', ')}. Choose a store with
                  one of these traits to run the preview.
                </p>
              )}
              <p className="hint">
                Evaluates {previewRule.balance_level} totals at order {previewRule.execution_order}
                {previewRule.stop_processing
                  ? ' and stops later configured threshold rules if it triggers.'
                  : '; later configured threshold rules continue.'}
              </p>
              <div className="cfg-form">
                <StoreSelect
                  value={previewStore}
                  emptyLabel="All stores"
                  onChange={(value) => {
                    setPreviewStore(value);
                    setPreviewData(null);
                  }}
                />
                <Field label="From business date">
                  <input
                    type="date"
                    value={previewFrom}
                    onChange={(event) => {
                      setPreviewFrom(event.target.value);
                      setPreviewData(null);
                    }}
                  />
                </Field>
                <Field label="Through business date">
                  <input
                    type="date"
                    value={previewTo}
                    onChange={(event) => {
                      setPreviewTo(event.target.value);
                      setPreviewData(null);
                    }}
                  />
                </Field>
                <div className="cfg-actions">
                  <Button
                    variant="primary"
                    disabled={
                      previewBusy ||
                      !previewFrom ||
                      !previewTo ||
                      previewFrom > previewTo ||
                      (previewRule.location_traits.length > 0 && !previewStore)
                    }
                    onClick={() => void runRulePreview()}
                  >
                    {previewBusy ? 'Testing…' : 'Run preview'}
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setPreviewRule(null);
                      setPreviewData(null);
                    }}
                  >
                    Close preview
                  </Button>
                  <span className="hint">Up to 366 days. Only current totals are evaluated.</span>
                </div>
              </div>
              {previewData && (
                <>
                  <MetricGrid>
                    <MetricTile
                      label="Balances evaluated"
                      value={formatNumber(previewData.evaluated)}
                    />
                    <MetricTile
                      label="Would trigger"
                      value={formatNumber(previewData.would_breach)}
                    />
                    <MetricTile
                      label="Rule threshold"
                      value={
                        previewData.threshold_currency
                          ? money(previewData.threshold, previewData.threshold_currency)
                          : `${previewData.threshold} count`
                      }
                    />
                  </MetricGrid>
                  {previewData.evaluated > previewData.sample_limit && (
                    <p className="hint">
                      Showing the 50 most recent totals; counts include the full selected range.
                    </p>
                  )}
                  <DataTable
                    columns={previewColumns}
                    rows={previewData.samples}
                    rowKey={(row) =>
                      `${row.store_id}:${row.business_date}:${row.currency}:${row.dimension ?? 'store'}`
                    }
                    emptyTitle="No current totals in this range"
                    emptyHint="Recalculate the selected Store Days, then run the preview again."
                    csvFileName="variance-rule-preview-visible.csv"
                    detachTitle={`Rule preview · ${previewData.rule_name} · ${previewData.date_from} to ${previewData.date_to}`}
                  />
                </>
              )}
            </Card>
          )}
        </>
      )}
      {tab === 'Transaction rules' && (
        <TransactionRules locationTraitOptions={locationTraitOptions} />
      )}
      {tab === 'GL mapping' && (
        <Card title="GL cross-references">
          <p>
            Use these mappings to generate a read-only journal preview. Unmapped transactions remain
            visible for remediation.
          </p>
          <div className="cluster mt-sm">
            <Button size="sm" variant="secondary" onClick={() => void downloadGLPackage()}>
              Download mapping snapshot
            </Button>
            {(me?.role === 'finance' || me?.role === 'admin') && (
              <Button
                size="sm"
                variant="secondary"
                disabled={glPackageBusy}
                onClick={() => glPackageInput.current?.click()}
              >
                {glPackageBusy ? 'Preparing import…' : 'Import snapshot'}
              </Button>
            )}
            <input
              ref={glPackageInput}
              type="file"
              accept=".json,application/json"
              className="sr-only"
              aria-label="Choose a GL cross-reference snapshot"
              onChange={(event) => void previewGLPackage(event.currentTarget.files?.[0])}
            />
          </div>
          {glPackagePreview && (
            <div className="stack-sm mt-md">
              <h3>Snapshot preview: {glPackagePreview.fileName}</h3>
              <p>
                {glPackagePreview.result.counts.create} mapping(s) will be created,{' '}
                {glPackagePreview.result.counts.update} updated, and{' '}
                {glPackagePreview.result.counts.unchanged} left unchanged. Mappings absent from the
                file will remain as they are.
              </p>
              {glPackagePreview.result.errors.length > 0 && (
                <Alert tone="error">
                  {glPackagePreview.result.errors.map((item) => (
                    <div key={`${item.row}-${item.message}`}>
                      Row {item.row}: {item.message}
                    </div>
                  ))}
                </Alert>
              )}
              <DataTable
                rows={glPackagePreview.result.mappings}
                rowKey={(row) => `${row.row}-${row.transaction_type}-${row.tender_type ?? 'all'}`}
                columns={[
                  {
                    key: 'transaction',
                    header: 'Transaction',
                    primary: true,
                    render: (row) => row.transaction_type,
                    csvValue: (row) => row.transaction_type,
                  },
                  {
                    key: 'tender',
                    header: 'Tender',
                    render: (row) => row.tender_type || 'All',
                    csvValue: (row) => row.tender_type || 'All',
                  },
                  {
                    key: 'debit',
                    header: 'Debit',
                    render: (row) => row.debit_account,
                    csvValue: (row) => row.debit_account,
                  },
                  {
                    key: 'credit',
                    header: 'Credit',
                    render: (row) => row.credit_account,
                    csvValue: (row) => row.credit_account,
                  },
                  {
                    key: 'status',
                    header: 'Status',
                    render: (row) => (row.active ? 'Active' : 'Inactive'),
                    csvValue: (row) => (row.active ? 'Active' : 'Inactive'),
                  },
                  {
                    key: 'action',
                    header: 'Action',
                    render: (row) => row.action,
                    csvValue: (row) => row.action,
                  },
                ]}
                csvFileName="gl-mapping-import-preview-visible.csv"
                detachTitle={`GL mapping import preview · ${glPackagePreview.fileName}`}
              />
              <Field
                label="Reason for import"
                hint="At least 10 characters; recorded in the audit trail."
              >
                <textarea
                  value={glPackageReason}
                  maxLength={4000}
                  onChange={(event) => setGlPackageReason(event.target.value)}
                />
              </Field>
              <div className="cluster">
                <Button
                  size="sm"
                  variant="primary"
                  disabled={
                    !glPackagePreview.result.valid ||
                    glPackageBusy ||
                    glPackageReason.trim().length < 10 ||
                    (me?.role !== 'finance' && me?.role !== 'admin')
                  }
                  onClick={() => void applyGLPackage()}
                >
                  {glPackageBusy ? 'Applying…' : 'Apply snapshot'}
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={glPackageBusy}
                  onClick={() => setGlPackagePreview(null)}
                >
                  Cancel
                </Button>
              </div>
            </div>
          )}
          <div className="profile-form">
            <input
              value={gl.transaction_type}
              onChange={(e) => setGl({ ...gl, transaction_type: e.target.value })}
              placeholder="Transaction type"
            />
            <input
              value={gl.tender_type}
              onChange={(e) => setGl({ ...gl, tender_type: e.target.value })}
              placeholder="Tender (optional)"
            />
            <input
              value={gl.debit_account}
              onChange={(e) => setGl({ ...gl, debit_account: e.target.value })}
              placeholder="Debit GL account"
            />
            <input
              value={gl.credit_account}
              onChange={(e) => setGl({ ...gl, credit_account: e.target.value })}
              placeholder="Credit GL account"
            />
            <Button
              variant="primary"
              size="sm"
              disabled={
                !gl.transaction_type ||
                !gl.debit_account ||
                !gl.credit_account ||
                (me?.role !== 'finance' && me?.role !== 'admin') ||
                (editingGlId !== null && glReason.trim().length < 10)
              }
              onClick={() => void saveGL()}
            >
              {editingGlId ? 'Save mapping' : 'Add mapping'}
            </Button>
            {editingGlId && (
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  setEditingGlId(null);
                  setGlReason('');
                  setGl({
                    transaction_type: 'Sale',
                    tender_type: '',
                    debit_account: '',
                    credit_account: '',
                    active: true,
                  });
                }}
              >
                Cancel edit
              </Button>
            )}
          </div>
          {editingGlId && (
            <div className="stack-sm mt-sm">
              <label className="inline-check">
                <input
                  type="checkbox"
                  checked={gl.active}
                  onChange={(event) => setGl({ ...gl, active: event.target.checked })}
                />
                Active for journal previews
              </label>
              <Field
                label="Reason for change"
                hint="At least 10 characters; recorded in the audit trail."
              >
                <textarea
                  value={glReason}
                  maxLength={4000}
                  onChange={(event) => setGlReason(event.target.value)}
                />
              </Field>
            </div>
          )}
          <DataTable
            columns={[
              {
                key: 'transaction',
                header: 'Transaction',
                primary: true,
                render: (row) => row.transaction_type,
                csvValue: (row) => row.transaction_type,
              },
              {
                key: 'tender',
                header: 'Tender',
                render: (row) => row.tender_type || 'All',
                csvValue: (row) => row.tender_type || 'All',
              },
              {
                key: 'debit',
                header: 'Debit',
                render: (row) => row.debit_account,
                csvValue: (row) => row.debit_account,
              },
              {
                key: 'credit',
                header: 'Credit',
                render: (row) => row.credit_account,
                csvValue: (row) => row.credit_account,
              },
              {
                key: 'status',
                header: 'Status',
                render: (row) => (row.active ? 'Active' : 'Inactive'),
                csvValue: (row) => (row.active ? 'Active' : 'Inactive'),
              },
              {
                key: 'action',
                header: 'Action',
                render: (row) =>
                  me?.role === 'finance' || me?.role === 'admin' ? (
                    <Button size="sm" variant="secondary" onClick={() => editGL(row)}>
                      Edit
                    </Button>
                  ) : (
                    '—'
                  ),
                csvSkip: true,
              },
            ]}
            rows={maps}
            rowKey={(row) => row.id}
            csvFileName="gl-mappings-visible.csv"
            detachTitle="GL cross-reference mappings"
          />
          <h3 style={{ marginTop: '1.25rem' }}>Journal preview</h3>
          <p>
            Review proposed debits and credits before sending a journal through the ERP integration.
          </p>
          <div className="profile-form">
            <StoreSelect value={journalStore} onChange={setJournalStore} />
            <input
              type="date"
              value={journalDate}
              onChange={(e) => setJournalDate(e.target.value)}
            />
            <Button
              size="sm"

              disabled={!journalStore}
              onClick={() => void loadJournal(1)}
            >
              Preview journal
            </Button>
          </div>
          {journal && (
            <>
              <p className="page-sub">
                Page {journal.page} of {journal.pages} · {journal.lines.length} mapped entries and{' '}
                {journal.unmapped_transaction_ids.length} unmapped transactions on this page ·{' '}
                {formatNumber(journal.total_transactions)} transactions total.
              </p>
              <ControlsTable
                headers={['Transaction', 'Debit', 'Credit', 'Amount', 'Currency']}
                rows={journal.lines.map((x) => [
                  x.transaction_id,
                  x.debit_account,
                  x.credit_account,
                  money(x.amount, x.currency),
                  x.currency,
                ])}
              />
              {journal.unmapped_transaction_ids.length > 0 && (
                <Alert tone="warning">
                  Unmapped transaction IDs: {journal.unmapped_transaction_ids.join(', ')}
                </Alert>
              )}
              <div className="button-row">
                <Button
                  size="sm"
                  disabled={journalPage <= 1}
                  onClick={() => void loadJournal(journalPage - 1)}
                >
                  Previous page
                </Button>
                <Button
                  size="sm"
                  disabled={journalPage >= journal.pages}
                  onClick={() => void loadJournal(journalPage + 1)}
                >
                  Next page
                </Button>
              </div>
            </>
          )}
        </Card>
      )}
      {tab === 'Error codes' && <ErrorDefinitionPackages />}
    </SettingsSection>
  );
}
