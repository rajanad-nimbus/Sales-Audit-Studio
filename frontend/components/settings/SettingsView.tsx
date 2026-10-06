'use client';

import { useEffect, useState } from 'react';
import { useTheme } from '@/lib/ThemeContext';
import { useMe } from '@/components/MeProvider';
import { api } from '@/lib/api';
import { useToast } from '@/components/Feedback';

const ALL_TABS = [['appearance', 'Appearance'], ['account', 'Account'], ['integrations', 'Source feeds & integrations'], ['reconciliation', 'Reconciliation tolerance'], ['readiness', 'Production readiness']] as const;
type Tab = typeof ALL_TABS[number][0];
const SCOPES = { general: ['appearance', 'account'], integrations: ['integrations', 'reconciliation', 'readiness'] } as const;

const OPTIONS = [
  { value: 'light', label: 'Light', note: 'Bright surfaces, best in daylight.' },
  { value: 'dark', label: 'Dark', note: 'Low-glare navy surfaces.' },
  { value: 'system', label: 'System', note: 'Follow your device setting.' },
] as const;

export function SettingsView({ scope }: { scope: 'general' | 'integrations' }) {
  const TABS = ALL_TABS.filter(([k]) => (SCOPES[scope] as readonly string[]).includes(k));
  const { theme, setTheme } = useTheme();
  const { me } = useMe();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>(TABS[0][0]);
  const [mounted, setMounted] = useState(false);
  const [profiles, setProfiles] = useState<{ id: string; name: string; source_system: string; schedule: string | null; enabled: boolean; updated_at: string }[]>([]);
  const [ontology, setOntology] = useState<{ configured: boolean; source: string; last_error: string | null; approved_rule_codes: string[]; expected_rule_codes: string[] } | null>(null);
  const [readiness, setReadiness] = useState<{ ready: boolean; checks: { name: string; ready: boolean; detail: string }[]; sources: { source_system: string; received_at: string | null; ready: boolean }[]; next_inputs: string[] } | null>(null);
  const [reconciliationPolicy, setReconciliationPolicy] = useState<{ amount_tolerance: number; settlement_day_tolerance: number; source: string; updated_by: string | null } | null>(null);
  const [integrationError, setIntegrationError] = useState<string | null>(null);
  const [profileName, setProfileName] = useState('');
  const [profileSource, setProfileSource] = useState('POS');
  const [profileSchedule, setProfileSchedule] = useState('daily');
  const [profileMapping, setProfileMapping] = useState('{}');
  const [shopifyStatus, setShopifyStatus] = useState<{ connector: string; status: string; last_sync_timestamp: string | null; quarantined_records: number; hours_since_last_sync: number | null } | null>(null);
  const [shopifyError, setShopifyError] = useState<string | null>(null);
  const [shopifySyncing, setShopifySyncing] = useState(false);
  useEffect(() => {
    setMounted(true);
    const h = window.location.hash.slice(1) as Tab;
    if (TABS.some(([k]) => k === h)) setTab(h);
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  const pick = (t: Tab) => { setTab(t); history.replaceState(null, '', `#${t}`); };
  const mappingError = (() => { try { JSON.parse(profileMapping); return null; } catch { return 'Not valid JSON yet.'; } })();
  useEffect(() => { if (scope !== 'integrations') return; Promise.all([api.get('/api/ingest/profiles'), api.get('/api/ontology/integration/status'), api.get('/api/production-readiness'), api.get('/api/reconciliation/policy')])
    .then(([p, o, r, policy]) => { setProfiles(p.data); setOntology(o.data); setReadiness(r.data); setReconciliationPolicy(policy.data); }).catch(() => setIntegrationError('Unable to load protected operational configuration.')); }, []);
  useEffect(() => { if (scope !== 'integrations') return; api.get('/api/ingest/shopify/status').then((res) => setShopifyStatus(res.data)).catch(() => setShopifyStatus(null)); }, []);
  const refreshOntology = async () => { try { setIntegrationError(null); const { data } = await api.post('/api/ontology/integration/refresh'); setOntology(data); toast('Approved policy refreshed.', 'success'); } catch (e: any) { setIntegrationError(e?.response?.data?.detail ?? 'Ontology refresh failed'); } };
  const reloadProfiles = async () => setProfiles((await api.get('/api/ingest/profiles')).data);
  const createProfile = async () => {
    try {
      const column_mapping = JSON.parse(profileMapping);
      await api.post('/api/ingest/profiles', { name: profileName, source_system: profileSource, schedule: profileSchedule || null, column_mapping });
      setProfileName(''); setProfileMapping('{}'); await reloadProfiles(); toast('Profile saved.', 'success');
    } catch (e: any) { setIntegrationError(e instanceof SyntaxError ? 'Column mapping must be valid JSON.' : e?.response?.data?.detail ?? 'Could not create profile'); }
  };
  const toggleProfile = async (id: string, enabled: boolean) => { try { await api.patch(`/api/ingest/profiles/${id}`, { enabled: !enabled }); await reloadProfiles(); toast(enabled ? 'Profile disabled.' : 'Profile enabled.', 'success'); } catch (e: any) { setIntegrationError(e?.response?.data?.detail ?? 'Could not update profile'); } };
  const savePolicy = async () => { if (!reconciliationPolicy) return; try { const { data } = await api.put('/api/reconciliation/policy', reconciliationPolicy); setReconciliationPolicy(data); toast('Reconciliation tolerance saved.', 'success'); } catch (e: any) { setIntegrationError(e?.response?.data?.detail ?? 'Could not save reconciliation policy'); } };
  const syncShopify = async () => {
    try {
      setShopifySyncing(true);
      setShopifyError(null);
      const today = new Date();
      const yesterday = new Date(today.getTime() - 24 * 60 * 60 * 1000);
      await api.post('/api/ingest/shopify/sync', {
        created_at_min: yesterday.toISOString(),
        created_at_max: today.toISOString(),
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

  return (
    <div className="page-container" style={{ maxWidth: 920, marginLeft: 0 }}>
      <div className="cc-seg" role="tablist" aria-label="Sections" style={{ alignSelf: 'flex-start', flexWrap: 'wrap' }}>
        {TABS.map(([k, label]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => pick(k)}>{label}</button>)}
      </div>
      {integrationError && <div className="alert alert-error" role="alert">{integrationError}<button className="btn-secondary btn-sm alert-close" onClick={() => setIntegrationError(null)} aria-label="Dismiss">×</button></div>}

      {tab === 'appearance' && <section className="info-card">
        <h2>Appearance</h2>
        <p>Choose how Nimbus looks. Your choice is saved in this browser.</p>
        <div className="theme-options" role="radiogroup" aria-label="Theme">
          {OPTIONS.map((o) => (
            <button key={o.value} role="radio" aria-checked={mounted && theme === o.value}
              className={`theme-option${mounted && theme === o.value ? ' on' : ''}`} onClick={() => setTheme(o.value)}>
              <b>{o.label}</b><small>{o.note}</small>
            </button>
          ))}
        </div>
      </section>}

      {tab === 'readiness' && <section className="info-card">
        <h2>Production readiness</h2>
        <p>{readiness?.ready ? 'Nimbus is ready for governed cutover.' : 'Nimbus capabilities are ready; external production prerequisites remain.'}</p>
        {readiness && <><ul className="summary-list">{readiness.checks.map((check) => <li key={check.name}><span className={`badge badge-sm ${check.ready ? 'badge-success' : 'badge-warning'}`}>{check.ready ? 'Ready' : 'Pending'}</span> <b>{check.name}</b> — {check.detail}</li>)}</ul>
        <div className="table-container"><table className="data-table"><thead><tr><th>Required feed</th><th>Latest immutable delivery</th><th>Status</th></tr></thead><tbody>{readiness.sources.map((source) => <tr key={source.source_system}><td>{source.source_system}</td><td>{source.received_at ? new Date(source.received_at).toLocaleString() : 'Not received'}</td><td><span className={`badge badge-sm ${source.ready ? 'badge-success' : 'badge-warning'}`}>{source.ready ? 'Received' : 'Pending'}</span></td></tr>)}</tbody></table></div>
        <h3 style={{ marginTop: '1rem' }}>Inputs still needed</h3><ul className="summary-list">{readiness.next_inputs.map((input) => <li key={input}>{input}</li>)}</ul></>}
      </section>}

      {tab === 'integrations' && <><section className="info-card">
        <h2>Source-feed profiles</h2>
        <p>Profiles define the read-only source mapping and expected delivery schedule. Deliveries and errors appear in the Data Pipeline.</p>
        {profiles.length === 0 ? <p>No source-feed profiles configured.</p> : <div className="table-container"><table className="data-table"><thead><tr><th>Name</th><th>Source</th><th>Schedule</th><th>Status</th><th>Updated</th><th /></tr></thead><tbody>{profiles.map((profile) => <tr key={profile.id}><td>{profile.name}</td><td>{profile.source_system}</td><td>{profile.schedule || 'On delivery'}</td><td><span className={`badge badge-sm ${profile.enabled ? 'badge-success' : 'badge-secondary'}`}>{profile.enabled ? 'Enabled' : 'Disabled'}</span></td><td>{new Date(profile.updated_at).toLocaleString()}</td><td><button className="btn-secondary btn-small" onClick={() => toggleProfile(profile.id, profile.enabled)}>{profile.enabled ? 'Disable' : 'Enable'}</button></td></tr>)}</tbody></table></div>}
        <h3 style={{ marginTop: '1rem' }}>Add source-feed profile</h3>
        <div className="profile-form"><input value={profileName} onChange={(e) => setProfileName(e.target.value)} placeholder="Profile name" /><select value={profileSource} onChange={(e) => setProfileSource(e.target.value)}>{['POS', 'POSControl', 'Processor', 'Bank', 'ERP', 'Shopify'].map((source) => <option key={source}>{source}</option>)}</select><input value={profileSchedule} onChange={(e) => setProfileSchedule(e.target.value)} placeholder="daily, hourly, weekly" /><input value={profileMapping} onChange={(e) => setProfileMapping(e.target.value)} placeholder='{"source_record_id":"transaction_id"}' aria-invalid={!!mappingError} aria-label="Column mapping (JSON)" title={mappingError ?? 'Column mapping JSON'} style={mappingError ? { borderColor: 'var(--error)' } : undefined} /><button disabled={!profileName.trim() || !!mappingError} className="btn-primary btn-small" onClick={createProfile}>Save profile</button></div>
      </section>

      <section className="info-card">
        <h2>Shopify integration</h2>
        <p>Sync orders and refunds from your Shopify store for unified financial reconciliation.</p>
        {shopifyStatus ? (
          <>
            <ul className="summary-list">
              <li>Status: <b>{({ healthy: 'Healthy', stale: 'Stale: no sync in over 26 hours', no_syncs: 'No syncs yet' } as Record<string, string>)[shopifyStatus.status] ?? shopifyStatus.status}</b></li>
              {shopifyStatus.last_sync_timestamp && <li>Last sync: <b>{new Date(shopifyStatus.last_sync_timestamp).toLocaleString()}</b> ({shopifyStatus.hours_since_last_sync != null && shopifyStatus.hours_since_last_sync >= 1 ? `${Math.round(shopifyStatus.hours_since_last_sync)}h ago` : 'less than an hour ago'})</li>}
              {shopifyStatus.quarantined_records > 0 && <li>Quarantined records: <b>{shopifyStatus.quarantined_records}</b> (review in Data Pipeline)</li>}
            </ul>
            {shopifyError && <p className="alert alert-error">{shopifyError}</p>}
            <button className="btn-secondary btn-small" onClick={syncShopify} disabled={shopifySyncing}>{shopifySyncing ? 'Syncing…' : 'Sync now'}</button>
          </>
        ) : (
          <p className="alert alert-warning">Shopify status is unavailable. Check that the backend has SHOPIFY_STORE_URL and credentials set, and that you have the Finance or IT role.</p>
        )}
      </section>

      <section className="info-card">
        <h2>Ontology policy integration</h2>
        <p>Nimbus reads approved Sales Audit rules only; it never edits or promotes Ontology content.</p>
        {ontology && <ul className="summary-list"><li>Connection: <b>{ontology.configured ? 'Configured' : 'Not configured'}</b></li><li>Policy source: <b>{ontology.source}</b></li><li>Approved rules loaded: <b>{ontology.approved_rule_codes.length} of {ontology.expected_rule_codes.length}</b></li>{ontology.last_error && <li>Last result: {ontology.last_error}</li>}</ul>}
        <button className="btn-secondary btn-small" onClick={refreshOntology}>Refresh approved policy</button>
      </section>
      </>}

      {tab === 'reconciliation' && <section className="info-card">
        <h2>Reconciliation tolerance</h2>
        <p>Fallback controls used until Nimbus receives approved tolerance bands from Ontology.</p>
        {reconciliationPolicy && <div className="profile-form"><label>Amount tolerance<input type="number" min="0" step="0.01" value={reconciliationPolicy.amount_tolerance} onChange={(e) => setReconciliationPolicy({ ...reconciliationPolicy, amount_tolerance: Number(e.target.value) })} /></label><label>Settlement days<input type="number" min="0" max="31" value={reconciliationPolicy.settlement_day_tolerance} onChange={(e) => setReconciliationPolicy({ ...reconciliationPolicy, settlement_day_tolerance: Number(e.target.value) })} /></label><span /><span /><button className="btn-secondary btn-small" onClick={savePolicy}>Save tolerance</button></div>}
      </section>}

      {tab === 'account' && <section className="info-card">
        <h2>Account</h2>
        <ul className="summary-list">
          <li>User: <b>{me === undefined ? '…' : me ? me.user ?? 'Demo user' : 'Signed out'}</b></li>
          <li>Role: <b>{me ? me.role : '-'}</b></li>
          <li>Authentication and roles come from Ontology Studio. Sign in or out from the user menu in the top-right corner.</li>
        </ul>
      </section>}

    </div>
  );
}
