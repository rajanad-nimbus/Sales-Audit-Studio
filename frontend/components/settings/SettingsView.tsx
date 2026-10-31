'use client';

import { useEffect, useState } from 'react';
import { useTheme } from '@/lib/ThemeContext';
import { useMe } from '@/components/MeProvider';
import { api } from '@/lib/api';
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

const ALL_TABS = [
  ['appearance', 'Appearance'],
  ['account', 'Account'],
  ['integrations', 'Connections'],
  ['reconciliation', 'Matching tolerance'],
  ['readiness', 'Go-live readiness'],
] as const;
type Tab = (typeof ALL_TABS)[number][0];
const SCOPES = {
  general: ['appearance', 'account'],
  integrations: ['integrations', 'reconciliation', 'readiness'],
} as const;

const OPTIONS = [
  { value: 'light', label: 'Light', note: 'Bright surfaces, best in daylight.' },
  { value: 'dark', label: 'Dark', note: 'Low-glare navy surfaces.' },
  { value: 'system', label: 'System', note: 'Follow your device setting.' },
] as const;

const SYNC_RANGES = [
  { hours: 24, label: 'Last 24 hours' },
  { hours: 24 * 7, label: 'Last 7 days' },
  { hours: 24 * 30, label: 'Last 30 days' },
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
  const [profiles, setProfiles] = useState<
    {
      id: string;
      name: string;
      source_system: string;
      schedule: string | null;
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
    settlement_day_tolerance: number;
    gap_min_count: number;
    gap_share: number | string;
    source: string;
    updated_by: string | null;
  } | null>(null);
  const [integrationError, setIntegrationError] = useState<string | null>(null);
  const [profileName, setProfileName] = useState('');
  const [profileSource, setProfileSource] = useState('POS');
  const [profileSchedule, setProfileSchedule] = useState('daily');
  const [profileMapping, setProfileMapping] = useState('{}');
  const [shopifyStatus, setShopifyStatus] = useState<{
    connector: string;
    status: string;
    last_sync_timestamp: string | null;
    quarantined_records: number;
    hours_since_last_sync: number | null;
  } | null>(null);
  const [shopifyLoaded, setShopifyLoaded] = useState(false);
  const [shopifyError, setShopifyError] = useState<string | null>(null);
  const [shopifySyncing, setShopifySyncing] = useState(false);
  const [syncHours, setSyncHours] = useState<number>(24);
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
      JSON.parse(profileMapping);
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
    ])
      .then(([p, o, r, policy]) => {
        setProfiles(p.data);
        setOntology(o.data);
        setReadiness(r.data);
        setReconciliationPolicy(policy.data);
      })
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
  const reloadProfiles = async () => setProfiles((await api.get('/api/ingest/profiles')).data);
  const createProfile = async () => {
    try {
      const column_mapping = JSON.parse(profileMapping);
      await api.post('/api/ingest/profiles', {
        name: profileName,
        source_system: profileSource,
        schedule: profileSchedule || null,
        column_mapping,
      });
      setProfileName('');
      setProfileMapping('{}');
      await reloadProfiles();
      toast('Profile saved.', 'success');
    } catch (e: any) {
      setIntegrationError(
        e instanceof SyntaxError
          ? 'Column mapping must be valid JSON.'
          : (e?.response?.data?.detail ?? 'Could not create profile')
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
    if (!reconciliationPolicy) return;
    try {
      const { data } = await api.put('/api/reconciliation/policy', reconciliationPolicy);
      setReconciliationPolicy(data);
      toast('Matching tolerance saved.', 'success');
    } catch (e: any) {
      setIntegrationError(e?.response?.data?.detail ?? 'Could not save reconciliation policy');
    }
  };
  const syncShopify = async () => {
    try {
      setShopifySyncing(true);
      setShopifyError(null);
      const now = new Date();
      const from = new Date(now.getTime() - syncHours * 60 * 60 * 1000);
      await api.post('/api/ingest/shopify/sync', {
        created_at_min: from.toISOString(),
        created_at_max: now.toISOString(),
      });
      const { data } = await api.get('/api/ingest/shopify/status');
      setShopifyStatus(data);
      toast('Shopify sync finished.', 'success');
    } catch (e: any) {
      setShopifyError(e?.response?.data?.detail ?? 'Shopify sync failed');
    } finally {
      setShopifySyncing(false);
    }
  };
  const setPolicy = (patch: Partial<NonNullable<typeof reconciliationPolicy>>) =>
    setReconciliationPolicy((p) => (p ? { ...p, ...patch } : p));

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
                        ? new Date(shopifyStatus.last_sync_timestamp).toLocaleString()
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
                    set aside. Review them in the Data Pipeline.
                  </Alert>
                )}
                {shopifyError && (
                  <Alert tone="error" onDismiss={() => setShopifyError(null)}>
                    {shopifyError}
                  </Alert>
                )}
                <div className="form-row">
                  <Field label="Orders to pull">
                    <select
                      value={syncHours}
                      onChange={(e) => setSyncHours(Number(e.target.value))}
                      disabled={shopifySyncing}
                    >
                      {SYNC_RANGES.map((r) => (
                        <option key={r.hours} value={r.hours}>
                          {r.label}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Button variant="primary" onClick={syncShopify} disabled={shopifySyncing}>
                    {shopifySyncing ? 'Syncing…' : 'Sync now'}
                  </Button>
                </div>
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
              and errors show in the Data Pipeline.
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
                  render: (p) => new Date(p.updated_at).toLocaleString(),
                },
                {
                  key: 'actions',
                  header: '',
                  render: (p) => (
                    <Button size="sm" onClick={() => toggleProfile(p.id, p.enabled)}>
                      {p.enabled ? 'Disable' : 'Enable'}
                    </Button>
                  ),
                },
              ]}
              rows={profiles}
              rowKey={(p) => p.id}
              emptyTitle="No source feeds yet"
              emptyHint="Add a feed below to describe a data source."
            />
            <details className="cfg-details">
              <summary>Add a source feed</summary>
              <div className="cfg-form">
                <Field label="Name">
                  <input
                    value={profileName}
                    onChange={(e) => setProfileName(e.target.value)}
                    placeholder="e.g. Store POS nightly"
                  />
                </Field>
                <Field label="Source">
                  <select value={profileSource} onChange={(e) => setProfileSource(e.target.value)}>
                    {['POS', 'POSControl', 'Processor', 'Bank', 'ERP', 'Shopify'].map((source) => (
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
                Save feed
              </Button>
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
        <Card title="Matching tolerance">
          <p className="hint">
            How closely records from different sources must agree to count as a match. Used until
            Ontology Studio supplies approved tolerances.
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
              <div>
                <Button variant="primary" onClick={savePolicy}>
                  Save tolerance
                </Button>
              </div>
            </>
          ) : (
            !integrationError && <p className="hint">Loading…</p>
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
                      source.received_at
                        ? new Date(source.received_at).toLocaleString()
                        : 'Not received',
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
    </SettingsSection>
  );
}
