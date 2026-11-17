'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useTheme } from '@/lib/ThemeContext';
import { useMe } from '@/components/MeProvider';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/preferences';
import { useToast } from '@/components/Feedback';
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
import type { BadgeVariant } from '@/components/ui';
import { SettingsSection } from './SettingsSection';
import { VoucherOptions } from './VoucherOptions';
import { VoucherRoutingPolicy } from './VoucherRoutingPolicy';
import { VoucherRetention } from './VoucherRetention';
import { VoucherPolicyPackages } from './VoucherPolicyPackages';
import { SourceFeedProfilePackages } from './SourceFeedProfilePackages';
import { ReconciliationPolicyPackages } from './ReconciliationPolicyPackages';
import { SystemOptionsPackages } from './SystemOptionsPackages';

const ALL_TABS = [
  ['appearance', 'Appearance'],
  ['account', 'Account'],
  ['integrations', 'Connections'],
  ['audit', 'Audit & exports'],
  ['reconciliation', 'Matching tolerance'],
  ['readiness', 'Go-live readiness'],
  ['import-policy', 'Import policy'],
  ['voucher-policy', 'Voucher policy'],
] as const;
type Tab = (typeof ALL_TABS)[number][0];
const SCOPES = {
  general: ['appearance', 'account'],
  integrations: [
    'integrations',
    'audit',
    'reconciliation',
    'readiness',
    'import-policy',
    'voucher-policy',
  ],
} as const;

const OPTIONS = [
  { value: 'light', label: 'Light', note: 'Bright surfaces, best in daylight.' },
  { value: 'dark', label: 'Dark', note: 'Low-glare navy surfaces.' },
  { value: 'system', label: 'System', note: 'Follow your device setting.' },
] as const;

const SHOPIFY_STATES: Record<string, { label: string; variant: BadgeVariant; note: string }> = {
  healthy: { label: 'Healthy', variant: 'success', note: 'Orders are syncing on schedule.' },
  stale: {
    label: 'Stale',
    variant: 'warning',
    note: 'No successful sync in over 26 hours. Check the connection or sync now.',
  },
  no_syncs: {
    label: 'No syncs yet',
    variant: 'secondary',
    note: 'Connected. Run a sync to pull your first orders.',
  },
};

const ago = (hours: number | null) =>
  hours == null ? '-' : hours < 1 ? 'Under 1 hour ago' : `${Math.round(hours)}h ago`;

export function SettingsView({ scope }: { scope: 'general' | 'integrations' }) {
  const TABS = ALL_TABS.filter(([k]) => (SCOPES[scope] as readonly string[]).includes(k));
  const { theme, setTheme } = useTheme();
  const { me } = useMe();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>(TABS[0][0]);
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get('tab');
    const allowedTabs = SCOPES[scope] as readonly string[];
    if (
      requested &&
      ALL_TABS.some(([key]) => key === requested && allowedTabs.includes(key))
    ) {
      setTab(requested as Tab);
    }
  }, [scope]);
  const [voucherPolicyVersion, setVoucherPolicyVersion] = useState(0);
  const [profiles, setProfiles] = useState<
    {
      id: string;
      name: string;
      source_system: string;
      schedule: string | null;
      column_mapping: Record<string, string>;
      enabled: boolean;
      updated_at: string;
    }[]
  >([]);
  const [ontology, setOntology] = useState<{
    configured: boolean;
    source: string;
    last_error: string | null;
    approved_rule_codes: string[];
    expected_rule_codes: string[];
  } | null>(null);
  const [readiness, setReadiness] = useState<{
    ready: boolean;
    checks: { name: string; ready: boolean; detail: string }[];
    sources: { source_system: string; received_at: string | null; ready: boolean }[];
    next_inputs: string[];
  } | null>(null);
  const [reconciliationPolicy, setReconciliationPolicy] = useState<{
    amount_tolerance: number;
    amount_tolerance_currency: string;
    settlement_day_tolerance: number;
    duplicate_lookback_days: number;
    gap_min_count: number;
    gap_share: number | string;
    source: string;
    updated_by: string | null;
  } | null>(null);
  const [reconciliationPolicyReason, setReconciliationPolicyReason] = useState('');
  const [exportUnit, setExportUnit] = useState<{
    unit_of_work: 'Transaction' | 'Store Day';
    updated_by: string | null;
  } | null>(null);
  const [backdatedTransactionLimit, setBackdatedTransactionLimit] = useState<{
    max_days: number | null;
    updated_by: string | null;
    updated_at: string | null;
  } | null>(null);
  const [duplicateMissingChecks, setDuplicateMissingChecks] = useState<{
    enabled: boolean;
    updated_by: string | null;
    updated_at: string | null;
  } | null>(null);
  const [auditAfterImport, setAuditAfterImport] = useState<{
    enabled: boolean;
    updated_by: string | null;
    updated_at: string | null;
  } | null>(null);
  const [cardSecurity, setCardSecurity] = useState<{
    security_level: 'Restricted Access' | 'Block';
    updated_by: string | null;
    updated_at: string | null;
  } | null>(null);
  const [savedCardSecurityLevel, setSavedCardSecurityLevel] = useState<string | null>(null);
  const [cardNumberMask, setCardNumberMask] = useState<{
    mask_character: string;
    updated_by: string | null;
    updated_at: string | null;
  } | null>(null);
  const [savedCardNumberMask, setSavedCardNumberMask] = useState<string | null>(null);
  const [savedAuditAfterImport, setSavedAuditAfterImport] = useState<boolean | null>(null);
  const [savedDuplicateMissingChecks, setSavedDuplicateMissingChecks] = useState<boolean | null>(
    null
  );
  const [maxBackdatedDays, setMaxBackdatedDays] = useState('');
  const [integrationError, setIntegrationError] = useState<string | null>(null);
  const [profileName, setProfileName] = useState('');
  const [profileSource, setProfileSource] = useState('POS');
  const [profileSchedule, setProfileSchedule] = useState('daily');
  const [profileMapping, setProfileMapping] = useState('{}');
  const [editingProfileId, setEditingProfileId] = useState<string | null>(null);
  const [shopifyStatus, setShopifyStatus] = useState<{
    connector: string;
    status: string;
    last_sync_timestamp: string | null;
    quarantined_records: number;
    hours_since_last_sync: number | null;
  } | null>(null);
  const [shopifyLoaded, setShopifyLoaded] = useState(false);
  useEffect(() => {
    setMounted(true);
    const h = window.location.hash.slice(1) as Tab;
    if (TABS.some(([k]) => k === h)) setTab(h);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const pick = (t: string) => {
    setTab(t as Tab);
    history.replaceState(null, '', `#${t}`);
  };
  const mappingError = (() => {
    try {
      const mapping = JSON.parse(profileMapping);
      if (!mapping || typeof mapping !== 'object' || Array.isArray(mapping))
        return 'Use a JSON object that maps Nimbus fields to source columns.';
      const entries = Object.entries(mapping);
      if (entries.length > 100) return 'Use no more than 100 mapped fields.';
      if (
        entries.some(
          ([key, value]) =>
            !key.trim() ||
            key.trim().length > 100 ||
            typeof value !== 'string' ||
            !value.trim() ||
            value.trim().length > 100
        )
      )
        return 'Mapping keys and values must be non-blank text of at most 100 characters.';
      return null;
    } catch {
      return 'Not valid JSON yet.';
    }
  })();
  useEffect(() => {
    if (scope !== 'integrations') return;
    Promise.all([
      api.get('/api/ingest/profiles'),
      api.get('/api/ontology/integration/status'),
      api.get('/api/production-readiness'),
      api.get('/api/reconciliation/policy'),
      api.get('/api/system-options/export-unit-of-work'),
      api.get('/api/system-options/backdated-transaction-limit'),
      api.get('/api/system-options/duplicate-missing-transaction-checks'),
      api.get('/api/system-options/audit-after-import'),
      api.get('/api/system-options/card-security'),
      api.get('/api/system-options/card-number-mask'),
    ])
      .then(
        ([
          p,
          o,
          r,
          policy,
          exportSettings,
          backdatedLimit,
          duplicateChecks,
          auditSettings,
          cardSecuritySettings,
          cardMaskSettings,
        ]) => {
          setProfiles(p.data);
          setOntology(o.data);
          setReadiness(r.data);
          setReconciliationPolicy(policy.data);
          setExportUnit(exportSettings.data);
          setBackdatedTransactionLimit(backdatedLimit.data);
          setDuplicateMissingChecks(duplicateChecks.data);
          setSavedDuplicateMissingChecks(duplicateChecks.data.enabled);
          setAuditAfterImport(auditSettings.data);
          setSavedAuditAfterImport(auditSettings.data.enabled);
          setCardSecurity(cardSecuritySettings.data);
          setSavedCardSecurityLevel(cardSecuritySettings.data.security_level);
          setCardNumberMask(cardMaskSettings.data);
          setSavedCardNumberMask(cardMaskSettings.data.mask_character);
          setMaxBackdatedDays(
            backdatedLimit.data.max_days == null ? '' : String(backdatedLimit.data.max_days)
          );
        }
      )
      .catch(() => setIntegrationError('Unable to load protected operational configuration.'));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (scope !== 'integrations') return;
    api
      .get('/api/ingest/shopify/status')
      .then((res) => setShopifyStatus(res.data))
      .catch(() => setShopifyStatus(null))
      .finally(() => setShopifyLoaded(true));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const refreshOntology = async () => {
    try {
      setIntegrationError(null);
      const { data } = await api.post('/api/ontology/integration/refresh');
      setOntology(data);
      toast('Approved policy refreshed.', 'success');
    } catch (e: any) {
      setIntegrationError(e?.response?.data?.detail ?? 'Ontology refresh failed');
    }
  };
  const reloadSystemOptions = async () => {
    try {
      setIntegrationError(null);
      const [
        exportSettings,
        backdatedLimit,
        duplicateChecks,
        auditSettings,
        cardSecuritySettings,
        cardMaskSettings,
      ] = await Promise.all([
        api.get('/api/system-options/export-unit-of-work'),
        api.get('/api/system-options/backdated-transaction-limit'),
        api.get('/api/system-options/duplicate-missing-transaction-checks'),
        api.get('/api/system-options/audit-after-import'),
        api.get('/api/system-options/card-security'),
        api.get('/api/system-options/card-number-mask'),
      ]);
      setExportUnit(exportSettings.data);
      setBackdatedTransactionLimit(backdatedLimit.data);
      setDuplicateMissingChecks(duplicateChecks.data);
      setSavedDuplicateMissingChecks(duplicateChecks.data.enabled);
      setAuditAfterImport(auditSettings.data);
      setSavedAuditAfterImport(auditSettings.data.enabled);
      setCardSecurity(cardSecuritySettings.data);
      setSavedCardSecurityLevel(cardSecuritySettings.data.security_level);
      setCardNumberMask(cardMaskSettings.data);
      setSavedCardNumberMask(cardMaskSettings.data.mask_character);
      setMaxBackdatedDays(
        backdatedLimit.data.max_days == null ? '' : String(backdatedLimit.data.max_days)
      );
    } catch {
      setIntegrationError('System options were applied, but the settings view could not refresh.');
    }
  };
  const reloadProfiles = async () => setProfiles((await api.get('/api/ingest/profiles')).data);
  const createProfile = async () => {
    try {
      const column_mapping = JSON.parse(profileMapping);
      if (editingProfileId) {
        await api.patch(`/api/ingest/profiles/${editingProfileId}`, {
          schedule: profileSchedule || null,
          column_mapping,
        });
      } else {
        await api.post('/api/ingest/profiles', {
          name: profileName,
          source_system: profileSource,
          schedule: profileSchedule || null,
          column_mapping,
        });
      }
      setProfileName('');
      setProfileMapping('{}');
      setProfileSchedule('daily');
      setEditingProfileId(null);
      await reloadProfiles();
      toast(editingProfileId ? 'Feed profile updated.' : 'Profile saved.', 'success');
    } catch (e: any) {
      setIntegrationError(
        e instanceof SyntaxError
          ? 'Column mapping must be valid JSON.'
          : (e?.response?.data?.detail ??
              (editingProfileId ? 'Could not update profile.' : 'Could not create profile.'))
      );
    }
  };
  const toggleProfile = async (id: string, enabled: boolean) => {
    try {
      await api.patch(`/api/ingest/profiles/${id}`, { enabled: !enabled });
      await reloadProfiles();
      toast(enabled ? 'Profile disabled.' : 'Profile enabled.', 'success');
    } catch (e: any) {
      setIntegrationError(e?.response?.data?.detail ?? 'Could not update profile');
    }
  };
  const savePolicy = async () => {
    if (!reconciliationPolicy || reconciliationPolicyReason.trim().length < 10) return;
    try {
      const { data } = await api.put('/api/reconciliation/policy', {
        ...reconciliationPolicy,
        reason: reconciliationPolicyReason.trim(),
      });
      setReconciliationPolicy(data);
      setReconciliationPolicyReason('');
      toast('Reconciliation policy saved.', 'success');
    } catch (e: any) {
      setIntegrationError(e?.response?.data?.detail ?? 'Could not save reconciliation policy');
    }
  };
  const setPolicy = (patch: Partial<NonNullable<typeof reconciliationPolicy>>) =>
    setReconciliationPolicy((p) => (p ? { ...p, ...patch } : p));
  const saveExportUnit = async () => {
    if (!exportUnit) return;
    try {
      const { data } = await api.put('/api/system-options/export-unit-of-work', exportUnit);
      setExportUnit(data);
      toast('Export unit of work saved.', 'success');
    } catch (e: any) {
      setIntegrationError(e?.response?.data?.detail ?? 'Could not save export unit of work');
    }
  };

  const saveBackdatedTransactionLimit = async () => {
    if (!backdatedTransactionLimit) return;
    try {
      setIntegrationError(null);
      const { data } = await api.put('/api/system-options/backdated-transaction-limit', {
        max_days: maxBackdatedDays === '' ? null : Number(maxBackdatedDays),
      });
      setBackdatedTransactionLimit(data);
      setMaxBackdatedDays(data.max_days == null ? '' : String(data.max_days));
      toast('Transaction import age limit saved.', 'success');
    } catch (e: any) {
      setIntegrationError(
        e?.response?.data?.detail ?? 'Could not save the transaction import age limit'
      );
    }
  };

  const saveDuplicateMissingChecks = async () => {
    if (!duplicateMissingChecks) return;
    try {
      setIntegrationError(null);
      const { data } = await api.put('/api/system-options/duplicate-missing-transaction-checks', {
        enabled: duplicateMissingChecks.enabled,
      });
      setDuplicateMissingChecks(data);
      setSavedDuplicateMissingChecks(data.enabled);
      toast('Duplicate and missing transaction checks saved.', 'success');
    } catch (e: any) {
      setIntegrationError(
        e?.response?.data?.detail ?? 'Could not save duplicate and missing transaction checks'
      );
    }
  };

  const saveAuditAfterImport = async () => {
    if (!auditAfterImport) return;
    try {
      setIntegrationError(null);
      const { data } = await api.put('/api/system-options/audit-after-import', {
        enabled: auditAfterImport.enabled,
      });
      setAuditAfterImport(data);
      setSavedAuditAfterImport(data.enabled);
      toast('Automatic audit after import saved.', 'success');
    } catch (e: any) {
      setIntegrationError(
        e?.response?.data?.detail ?? 'Could not save automatic audit after import'
      );
    }
  };

  const saveCardSecurity = async () => {
    if (!cardSecurity) return;
    try {
      setIntegrationError(null);
      const { data } = await api.put('/api/system-options/card-security', {
        security_level: cardSecurity.security_level,
      });
      setCardSecurity(data);
      setSavedCardSecurityLevel(data.security_level);
      toast('Card security policy saved.', 'success');
    } catch (e: any) {
      setIntegrationError(e?.response?.data?.detail ?? 'Could not save the card security policy');
    }
  };

  const saveCardNumberMask = async () => {
    if (!cardNumberMask) return;
    try {
      setIntegrationError(null);
      const { data } = await api.put('/api/system-options/card-number-mask', {
        mask_character: cardNumberMask.mask_character,
      });
      setCardNumberMask(data);
      setSavedCardNumberMask(data.mask_character);
      toast('Card number mask saved.', 'success');
    } catch (e: any) {
      setIntegrationError(e?.response?.data?.detail ?? 'Could not save the card number mask');
    }
  };

  const shopifyState = shopifyStatus
    ? (SHOPIFY_STATES[shopifyStatus.status] ?? {
        label: shopifyStatus.status,
        variant: 'secondary' as BadgeVariant,
        note: '',
      })
    : null;

  return (
    <SettingsSection
      title={scope === 'general' ? 'General' : 'Integrations'}
      description={
        scope === 'general'
          ? 'Your theme and account.'
          : 'Connect sales sources, set how closely records must match, and check go-live readiness.'
      }
    >
      <Tabs items={TABS.map(([id, label]) => ({ id, label }))} value={tab} onChange={pick} />
      {integrationError && (
        <Alert tone="error" onDismiss={() => setIntegrationError(null)}>
          {integrationError}
        </Alert>
      )}

      {tab === 'appearance' && (
        <Card title="Appearance">
          <p className="hint">Choose how Nimbus looks. Your choice is saved in this browser.</p>
          <div className="theme-options" role="radiogroup" aria-label="Theme">
            {OPTIONS.map((o) => (
              <button
                key={o.value}
                role="radio"
                aria-checked={mounted && theme === o.value}
                className={`theme-option${mounted && theme === o.value ? ' on' : ''}`}
                onClick={() => setTheme(o.value)}
              >
                <b>{o.label}</b>
                <small>{o.note}</small>
              </button>
            ))}
          </div>
        </Card>
      )}

      {tab === 'account' && (
        <Card title="Account">
          <dl className="kv">
            <dt>User</dt>
            <dd>{me === undefined ? '…' : me ? (me.user ?? 'Demo user') : 'Signed out'}</dd>
            <dt>Role</dt>
            <dd>{me ? me.role : '-'}</dd>
          </dl>
          <p className="hint cfg-note">
            Sign-in and roles come from Ontology Studio. Sign in or out from the user menu in the
            top-right corner.
          </p>
        </Card>
      )}

      {tab === 'integrations' && (
        <>
          <Card
            title={
              <span className="cfg-title">
                Shopify
                {shopifyState && (
                  <Badge variant={shopifyState.variant} size="sm">
                    {shopifyState.label}
                  </Badge>
                )}
              </span>
            }
          >
            <p className="hint">
              Pulls orders and refunds from your Shopify store so they are audited with every other
              sales source.
            </p>
            {shopifyStatus && shopifyState ? (
              <>
                <MetricGrid>
                  <MetricTile
                    label="Last sync"
                    value={
                      shopifyStatus.last_sync_timestamp
                        ? formatDateTime(shopifyStatus.last_sync_timestamp)
                        : 'Never'
                    }
                  />
                  <MetricTile
                    label="Time since sync"
                    value={ago(shopifyStatus.hours_since_last_sync)}
                  />
                  <MetricTile
                    label="Quarantined records"
                    value={shopifyStatus.quarantined_records}
                  />
                </MetricGrid>
                {shopifyState.note && <p className="hint">{shopifyState.note}</p>}
                {shopifyStatus.quarantined_records > 0 && (
                  <Alert tone="warning">
                    {shopifyStatus.quarantined_records} Shopify record(s) failed validation and were
                    set aside. Review them in Imports.
                  </Alert>
                )}
                <p className="hint">
                  This is the connection. To switch Shopify on or off, add it to the nightly batch,
                  or sync it now, go to <Link href="/imports#sources">Imports, Sources</Link>.
                </p>
              </>
            ) : shopifyLoaded ? (
              <Alert tone="warning">
                Shopify is not connected, or you do not have access. Ask IT to set the store URL and
                access token on the backend, and make sure you have the Finance or IT role.
              </Alert>
            ) : (
              <p className="hint">Checking connection…</p>
            )}
          </Card>

          <Card title="Source feeds">
            <p className="hint">
              Each feed says where sales data comes from and how often it should arrive. Deliveries
              and errors show in Imports.
            </p>
            <DataTable
              columns={[
                { key: 'name', header: 'Name', primary: true, render: (p) => p.name },
                { key: 'source', header: 'Source', render: (p) => p.source_system },
                { key: 'schedule', header: 'Schedule', render: (p) => p.schedule || 'On delivery' },
                {
                  key: 'status',
                  header: 'Status',
                  render: (p) => (
                    <Badge variant={p.enabled ? 'success' : 'secondary'} size="sm">
                      {p.enabled ? 'Enabled' : 'Disabled'}
                    </Badge>
                  ),
                },
                {
                  key: 'updated',
                  header: 'Updated',
                  nowrap: true,
                  render: (p) => formatDateTime(p.updated_at),
                },
                {
                  key: 'actions',
                  header: '',
                  render: (p) => (
                    <div className="action-buttons">
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => {
                          setEditingProfileId(p.id);
                          setProfileName(p.name);
                          setProfileSource(p.source_system);
                          setProfileSchedule(p.schedule ?? '');
                          setProfileMapping(JSON.stringify(p.column_mapping ?? {}, null, 2));
                        }}
                      >
                        Edit
                      </Button>
                      <Button size="sm" onClick={() => toggleProfile(p.id, p.enabled)}>
                        {p.enabled ? 'Disable' : 'Enable'}
                      </Button>
                    </div>
                  ),
                },
              ]}
              rows={profiles}
              rowKey={(p) => p.id}
              emptyTitle="No source feeds yet"
              emptyHint="Add a feed below to describe a data source."
            />
            <SourceFeedProfilePackages onApplied={reloadProfiles} />
            <details className="cfg-details" open={editingProfileId ? true : undefined}>
              <summary>{editingProfileId ? 'Edit source feed' : 'Add a source feed'}</summary>
              <div className="cfg-form">
                <Field label="Name">
                  <input
                    value={profileName}
                    onChange={(e) => setProfileName(e.target.value)}
                    placeholder="e.g. Store POS nightly"
                    disabled={!!editingProfileId}
                  />
                </Field>
                <Field label="Source">
                  <select
                    value={profileSource}
                    onChange={(e) => setProfileSource(e.target.value)}
                    disabled={!!editingProfileId}
                  >
                    {[
                      'POS',
                      'OMS',
                      'POSControl',
                      'OMSControl',
                      'Processor',
                      'Bank',
                      'ERP',
                      'Shopify',
                    ].map((source) => (
                      <option key={source}>{source}</option>
                    ))}
                  </select>
                </Field>
                <Field label="Schedule" hint="daily, hourly or weekly">
                  <input
                    value={profileSchedule}
                    onChange={(e) => setProfileSchedule(e.target.value)}
                  />
                </Field>
                <Field
                  label="Column mapping (JSON)"
                  hint={mappingError ?? 'Maps source columns to Nimbus fields'}
                >
                  <input
                    value={profileMapping}
                    onChange={(e) => setProfileMapping(e.target.value)}
                    placeholder='{"source_record_id":"transaction_id"}'
                    aria-invalid={!!mappingError}
                    style={mappingError ? { borderColor: 'var(--error)' } : undefined}
                  />
                </Field>
              </div>
              <Button
                variant="primary"
                disabled={!profileName.trim() || !!mappingError}
                onClick={createProfile}
              >
                {editingProfileId ? 'Update feed' : 'Save feed'}
              </Button>
              {editingProfileId && (
                <Button
                  variant="secondary"
                  onClick={() => {
                    setEditingProfileId(null);
                    setProfileName('');
                    setProfileSchedule('daily');
                    setProfileMapping('{}');
                  }}
                >
                  Cancel edit
                </Button>
              )}
            </details>
          </Card>

          <Card
            title={
              <span className="cfg-title">
                Ontology policy
                {ontology && (
                  <Badge variant={ontology.configured ? 'success' : 'secondary'} size="sm">
                    {ontology.configured ? 'Connected' : 'Not configured'}
                  </Badge>
                )}
              </span>
            }
          >
            <p className="hint">
              Nimbus reads approved Sales Audit rules from Ontology Studio. It never edits or
              approves them.
            </p>
            {ontology && (
              <dl className="kv">
                <dt>Policy source</dt>
                <dd>{ontology.source}</dd>
                <dt>Approved rules loaded</dt>
                <dd>
                  {ontology.approved_rule_codes.length} of {ontology.expected_rule_codes.length}
                </dd>
                {ontology.last_error && (
                  <>
                    <dt>Last result</dt>
                    <dd>{ontology.last_error}</dd>
                  </>
                )}
              </dl>
            )}
            <div>
              <Button onClick={refreshOntology}>Refresh approved policy</Button>
            </div>
          </Card>
        </>
      )}

      {tab === 'reconciliation' && (
        <Card title="Matching and duplicate controls">
          <p className="hint">
            Configure matching thresholds and duplicate sale detection for the next reconciliation.
          </p>
          {reconciliationPolicy ? (
            <>
              <div className="cfg-form">
                <Field
                  label="Amount tolerance"
                  hint="Largest difference in amount still treated as a match."
                >
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={reconciliationPolicy.amount_tolerance}
                    onChange={(e) => setPolicy({ amount_tolerance: Number(e.target.value) })}
                  />
                </Field>
                <Field label="Tolerance currency">
                  <input
                    maxLength={3}
                    value={reconciliationPolicy.amount_tolerance_currency}
                    onChange={(e) =>
                      setPolicy({ amount_tolerance_currency: e.target.value.toUpperCase() })
                    }
                  />
                </Field>
                <Field
                  label="Settlement days"
                  hint="How many days a payout may land after the sale."
                >
                  <input
                    type="number"
                    min="0"
                    max="31"
                    value={reconciliationPolicy.settlement_day_tolerance}
                    onChange={(e) =>
                      setPolicy({ settlement_day_tolerance: Number(e.target.value) })
                    }
                  />
                </Field>
                <Field
                  label="Duplicate lookback days"
                  hint="0 checks the same business date only. Up to 365 days compares exact POS sale details with earlier sales."
                >
                  <input
                    type="number"
                    min="0"
                    max="365"
                    value={reconciliationPolicy.duplicate_lookback_days}
                    onChange={(e) => setPolicy({ duplicate_lookback_days: Number(e.target.value) })}
                  />
                </Field>
                <Field
                  label="Large gap: minimum missing"
                  hint="Missing records needed to flag a gap."
                >
                  <input
                    type="number"
                    min="1"
                    value={reconciliationPolicy.gap_min_count}
                    onChange={(e) => setPolicy({ gap_min_count: Number(e.target.value) })}
                  />
                </Field>
                <Field
                  label="Large gap: share of declared (%)"
                  hint="Or the share of declared sales that is missing."
                >
                  <input
                    type="number"
                    min="1"
                    max="100"
                    step="1"
                    value={Math.round(Number(reconciliationPolicy.gap_share) * 100)}
                    onChange={(e) => setPolicy({ gap_share: Number(e.target.value) / 100 })}
                  />
                </Field>
              </div>
              <Field
                label="Reason for change"
                hint="This reason is recorded with the before and after policy values in the audit trail."
              >
                <textarea
                  rows={2}
                  maxLength={2000}
                  value={reconciliationPolicyReason}
                  onChange={(e) => setReconciliationPolicyReason(e.target.value)}
                />
              </Field>
              <div>
                <Button
                  variant="primary"
                  disabled={reconciliationPolicyReason.trim().length < 10}
                  onClick={savePolicy}
                >
                  Save tolerance
                </Button>
              </div>
              <hr />
              <ReconciliationPolicyPackages
                onApplied={async () => {
                  const { data } = await api.get('/api/reconciliation/policy');
                  setReconciliationPolicy(data);
                }}
              />
            </>
          ) : (
            !integrationError && <p className="hint">Loading…</p>
          )}
        </Card>
      )}

      {tab === 'audit' && (
        <Card title="Export unit of work">
          <p className="hint">
            Choose when sales transactions become eligible for downstream exports. Transaction mode
            delivers matched or manually matched transactions after detail checks pass; unresolved
            transactions and destination-blocking errors stay held. Store Day mode waits for the
            full day to close with current totals; run the export from Exports after closure.
          </p>
          {exportUnit ? (
            <div className="cfg-form">
              <Field label="Processing unit">
                <select
                  value={exportUnit.unit_of_work}
                  onChange={(event) =>
                    setExportUnit({
                      ...exportUnit,
                      unit_of_work: event.target.value as 'Transaction' | 'Store Day',
                    })
                  }
                >
                  <option value="Transaction">Transaction</option>
                  <option value="Store Day">Store Day</option>
                </select>
              </Field>
              <div className="cfg-actions">
                <Button
                  variant="primary"
                  disabled={!me || !['it', 'admin'].includes(me.role)}
                  onClick={() => void saveExportUnit()}
                >
                  Save export processing
                </Button>
                {me && !['it', 'admin'].includes(me.role) && (
                  <span className="hint">Only IT or admin can change this setting.</span>
                )}
                {exportUnit.updated_by && (
                  <span className="hint">Last changed by {exportUnit.updated_by}.</span>
                )}
              </div>
            </div>
          ) : (
            <p className="hint">Loading export settings…</p>
          )}
        </Card>
      )}

      {tab === 'readiness' && (
        <Card title="Go-live readiness">
          <p className="hint">
            {readiness?.ready
              ? 'Nimbus is ready for governed cutover.'
              : 'Nimbus itself is ready. Some external prerequisites below are still open.'}
          </p>
          {readiness && (
            <>
              <ul className="summary-list">
                {readiness.checks.map((check) => (
                  <li key={check.name}>
                    <Badge variant={check.ready ? 'success' : 'warning'} size="sm">
                      {check.ready ? 'Ready' : 'Pending'}
                    </Badge>{' '}
                    <b>{check.name}</b> — {check.detail}
                  </li>
                ))}
              </ul>
              <DataTable
                columns={[
                  {
                    key: 'feed',
                    header: 'Required feed',
                    primary: true,
                    render: (source) => source.source_system,
                  },
                  {
                    key: 'delivery',
                    header: 'Latest delivery',
                    render: (source) =>
                      source.received_at ? formatDateTime(source.received_at) : 'Not received',
                  },
                  {
                    key: 'status',
                    header: 'Status',
                    render: (source) => (
                      <Badge variant={source.ready ? 'success' : 'warning'} size="sm">
                        {source.ready ? 'Received' : 'Pending'}
                      </Badge>
                    ),
                  },
                ]}
                rows={readiness.sources}
                rowKey={(source) => String(source.source_system)}
              />
              {readiness.next_inputs.length > 0 && (
                <>
                  <h3>Still needed</h3>
                  <ul className="summary-list">
                    {readiness.next_inputs.map((input) => (
                      <li key={input}>{input}</li>
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
        </Card>
      )}

      {tab === 'import-policy' && (
        <>
          <Card title="Audit after each standalone import">
            <p className="hint">
              Run reconciliation for the affected stores and business dates as soon as an API,
              manual CSV, RTLOG, or Shopify delivery finishes. Scheduled batches still wait until
              all configured feeds have loaded before they audit.
            </p>
            <Alert tone="warning">
              An early delivery can raise missing-feed or balancing exceptions before later source
              files arrive. Keep this off when feeds for a store day arrive separately.
            </Alert>
            {auditAfterImport ? (
              <div className="form-row">
                <Field label="Reconcile immediately after each standalone delivery">
                  <input
                    type="checkbox"
                    checked={auditAfterImport.enabled}
                    disabled={!['it', 'admin'].includes(me?.role ?? '')}
                    onChange={(event) =>
                      setAuditAfterImport({ ...auditAfterImport, enabled: event.target.checked })
                    }
                  />
                </Field>
                {['it', 'admin'].includes(me?.role ?? '') && (
                  <Button
                    variant="primary"
                    disabled={savedAuditAfterImport === auditAfterImport.enabled}
                    onClick={() => void saveAuditAfterImport()}
                  >
                    Save audit timing
                  </Button>
                )}
                <span className="hint">
                  {auditAfterImport.updated_by
                    ? `Last changed by ${auditAfterImport.updated_by}${auditAfterImport.updated_at ? ` · ${formatDateTime(auditAfterImport.updated_at)}` : ''}`
                    : 'Default: off'}
                </span>
              </div>
            ) : (
              <p className="hint">Loading audit timing…</p>
            )}
          </Card>
          <Card title="Duplicate and missing transaction checks">
            <p className="hint">
              Audit same-source duplicate Sales from POS and OMS, plus gaps in POS register
              transaction numbers. OMS sequence gaps are not checked until the retailer’s numbering
              rules are configured. This does not change duplicate-delivery protection or Shopify
              duplicate checks.
            </p>
            {duplicateMissingChecks ? (
              <div className="form-row">
                <Field
                  label="Check duplicate sales and missing numbers"
                  hint="When disabled, reconciliation creates no new findings for these checks; existing findings remain open. Live POS number gaps still appear in Store Day review."
                >
                  <input
                    type="checkbox"
                    checked={duplicateMissingChecks.enabled}
                    disabled={!['it', 'admin'].includes(me?.role ?? '')}
                    onChange={(event) =>
                      setDuplicateMissingChecks({
                        ...duplicateMissingChecks,
                        enabled: event.target.checked,
                      })
                    }
                  />
                </Field>
                {['it', 'admin'].includes(me?.role ?? '') && (
                  <Button
                    variant="primary"
                    disabled={savedDuplicateMissingChecks === duplicateMissingChecks.enabled}
                    onClick={() => void saveDuplicateMissingChecks()}
                  >
                    Save audit checks
                  </Button>
                )}
                <span className="hint">
                  {duplicateMissingChecks.updated_by
                    ? `Last changed by ${duplicateMissingChecks.updated_by}${duplicateMissingChecks.updated_at ? ` · ${formatDateTime(duplicateMissingChecks.updated_at)}` : ''}`
                    : 'Default: enabled'}
                </span>
              </div>
            ) : (
              <p className="hint">Loading audit check settings…</p>
            )}
          </Card>
          <Card title="Payment card security">
            <p className="hint">
              ZeTSA always masks PAN-shaped references before canonical storage and display. Block
              mode quarantines transaction records and skips register events when they contain card
              numbers, verification data, track data, or a Luhn-valid PAN. Raw values are never
              retained.
            </p>
            {cardSecurity ? (
              <div className="form-row">
                <Field label="Ingestion policy">
                  <select
                    value={cardSecurity.security_level}
                    disabled={!['it', 'admin'].includes(me?.role ?? '')}
                    onChange={(event) =>
                      setCardSecurity({
                        ...cardSecurity,
                        security_level: event.target.value as 'Restricted Access' | 'Block',
                      })
                    }
                  >
                    <option value="Restricted Access">Restricted Access</option>
                    <option value="Block">Block cardholder data</option>
                  </select>
                </Field>
                {['it', 'admin'].includes(me?.role ?? '') && (
                  <Button
                    variant="primary"
                    disabled={savedCardSecurityLevel === cardSecurity.security_level}
                    onClick={() => void saveCardSecurity()}
                  >
                    Save security policy
                  </Button>
                )}
                <span className="hint">
                  {cardSecurity.updated_by
                    ? `Last changed by ${cardSecurity.updated_by}${cardSecurity.updated_at ? ` · ${formatDateTime(cardSecurity.updated_at)}` : ''}`
                    : 'Default: Restricted Access'}
                </span>
              </div>
            ) : (
              <p className="hint">Loading card security policy…</p>
            )}
            {cardNumberMask ? (
              <div className="form-row">
                <Field
                  label="Credit/debit card number mask"
                  hint="One visible, non-digit character. Four characters appear before the last four digits."
                >
                  <input
                    type="text"
                    maxLength={1}
                    value={cardNumberMask.mask_character}
                    disabled={!['it', 'admin'].includes(me?.role ?? '')}
                    onChange={(event) =>
                      setCardNumberMask({ ...cardNumberMask, mask_character: event.target.value })
                    }
                  />
                </Field>
                {['it', 'admin'].includes(me?.role ?? '') && (
                  <Button
                    variant="primary"
                    disabled={savedCardNumberMask === cardNumberMask.mask_character}
                    onClick={() => void saveCardNumberMask()}
                  >
                    Save mask
                  </Button>
                )}
                <span className="hint">
                  {cardNumberMask.updated_by
                    ? `Last changed by ${cardNumberMask.updated_by}${cardNumberMask.updated_at ? ` · ${formatDateTime(cardNumberMask.updated_at)}` : ''}`
                    : 'Default: •'}
                </span>
              </div>
            ) : (
              <p className="hint">Loading card number mask…</p>
            )}
          </Card>
          <Card title="Backdated transaction limit">
            <p className="hint">
              Limit how far back POS and Shopify transactions may be imported. Older records are
              retained in Imports as quarantined, with the source event date and reason. Leave the
              field blank to allow backdated transactions without a limit.
            </p>
            <div className="form-row">
              <Field
                label="Maximum age in days"
                hint="0 allows no backdating; future-dated records are unaffected. Maximum 3,650 days; blank means unlimited."
              >
                <input
                  type="number"
                  min="0"
                  max="3650"
                  step="1"
                  value={maxBackdatedDays}
                  disabled={!['it', 'admin'].includes(me?.role ?? '')}
                  onChange={(event) => setMaxBackdatedDays(event.target.value)}
                />
              </Field>
              {['it', 'admin'].includes(me?.role ?? '') && (
                <Button
                  variant="primary"
                  disabled={
                    !backdatedTransactionLimit ||
                    (maxBackdatedDays !== '' &&
                      (!/^[0-9]+$/.test(maxBackdatedDays) ||
                        Number(maxBackdatedDays) < 0 ||
                        Number(maxBackdatedDays) > 3650)) ||
                    (maxBackdatedDays === ''
                      ? backdatedTransactionLimit.max_days == null
                      : backdatedTransactionLimit.max_days === Number(maxBackdatedDays))
                  }
                  onClick={() => void saveBackdatedTransactionLimit()}
                >
                  Save import policy
                </Button>
              )}
              <span className="hint">
                {backdatedTransactionLimit?.updated_by
                  ? `Last changed by ${backdatedTransactionLimit.updated_by}${backdatedTransactionLimit.updated_at ? ` · ${formatDateTime(backdatedTransactionLimit.updated_at)}` : ''}`
                  : 'Default: unlimited'}
              </span>
            </div>
          </Card>
          <Card title="Promote system options">
            <SystemOptionsPackages onApplied={reloadSystemOptions} />
          </Card>
        </>
      )}

      {tab === 'voucher-policy' && (
        <>
          <VoucherOptions
            key={voucherPolicyVersion}
            onSaved={() => setVoucherPolicyVersion((version) => version + 1)}
          />
          <VoucherRoutingPolicy key={voucherPolicyVersion} />
          <VoucherRetention reloadKey={voucherPolicyVersion} />
          <Card title="Promote voucher policy">
            <VoucherPolicyPackages
              onApplied={async () => setVoucherPolicyVersion((version) => version + 1)}
            />
          </Card>
        </>
      )}
    </SettingsSection>
  );
}
