'use client';
import { useCallback, useEffect, useState } from 'react';
import { api, money } from '@/lib/api';
import { useToast } from '@/components/Feedback';
import { StoreSelect } from '@/components/StoreSelect';
import ControlsTable from '@/components/settings/ControlsTable';
import { Alert, Button, Card, Tabs } from '@/components/ui';
import { SettingsSection } from '@/components/settings/SettingsSection';
const STUDIO = process.env.NEXT_PUBLIC_ONTOLOGY_STUDIO_URL || 'http://localhost:3050';
type Total = {
  id: string;
  name: string;
  aggregation: string;
  source_system: string | null;
  transaction_types: string[];
  tender_type: string | null;
  level: string;
  revision: number;
  active: boolean;
  rollups: unknown[];
  calculation_basis: string;
  combined_total_ids: string[];
  combined_operator: string | null;
};
type Rule = {
  id: string;
  name: string;
  total_name: string;
  threshold: string;
  threshold_currency: string | null;
  severity: string;
  owner: string | null;
};
type GL = {
  id: string;
  transaction_type: string;
  tender_type: string | null;
  debit_account: string;
  credit_account: string;
  active: boolean;
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
const tabs = ['Totals & rules', 'GL mapping'] as const;
type Tab = (typeof tabs)[number];
export default function ControlsPage() {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>('Totals & rules');
  const [totals, setTotals] = useState<Total[]>([]),
    [rules, setRules] = useState<Rule[]>([]),
    [maps, setMaps] = useState<GL[]>([]),
    [error, setError] = useState<string | null>(null),
    [notice, setNotice] = useState<string | null>(null);
  const [gl, setGl] = useState({
      transaction_type: 'Sale',
      tender_type: '',
      debit_account: '',
      credit_account: '',
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
  const addGL = () =>
    save(
      () =>
        api.post('/api/gl/cross-references', {
          ...gl,
          tender_type: gl.tender_type || null,
          active: true,
        }),
      'GL mapping saved.'
    ).then(() => setGl({ ...gl, tender_type: '', debit_account: '', credit_account: '' }));
  return (
    <SettingsSection
      title="Totals, rules & GL mapping"
      description="Governed controls used when Store Days are recalculated. Stores, products and other reference data are under Data & Control → Foundation Data."
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
          <Alert
            tone="info"
            action={
              <a className="btn-secondary btn-sm" href={STUDIO} target="_blank" rel="noreferrer">
                Open Ontology Studio
              </a>
            }
          >
            Totals and rules are defined and approved in Ontology Studio. Nimbus shows them here
            read-only and applies the approved versions.
          </Alert>
          <Card title="Audit total definitions">
            <p>
              Active definitions are calculated and versioned during Store Day re-totaling. Rules
              reference <b>Configured: [definition name]</b>.
            </p>
            <ControlsTable
              headers={['Name', 'Aggregation', 'Source', 'Level', 'Filter']}
              rows={totals.map((x) => [
                x.name,
                x.calculation_basis === 'Combined'
                  ? `Combined (${x.combined_operator || '?'})`
                  : x.aggregation,
                x.source_system || 'Any',
                x.level,
                [...x.transaction_types, x.tender_type].filter(Boolean).join(', ') || 'All records',
              ])}
            />
          </Card>
          <Card title="Threshold rules">
            <p>
              A rule creates an investigation when the absolute target total exceeds the threshold
              on a re-total.
            </p>
            <ControlsTable
              headers={['Rule', 'Target', 'Threshold', 'Severity', 'Owner']}
              rows={rules.map((x) => [
                x.name,
                x.total_name,
                x.threshold_currency
                  ? money(x.threshold, x.threshold_currency)
                  : `${x.threshold} (count)`,
                x.severity,
                x.owner || 'Unassigned',
              ])}
            />
          </Card>
        </>
      )}
      {tab === 'GL mapping' && (
        <Card title="GL cross-references">
          <p>
            Use these mappings to generate a read-only journal preview. Unmapped transactions remain
            visible for remediation.
          </p>
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

              disabled={!gl.transaction_type || !gl.debit_account || !gl.credit_account}
              onClick={addGL}
            >
              Add mapping
            </Button>
          </div>
          <ControlsTable
            headers={['Transaction', 'Tender', 'Debit', 'Credit', 'Status']}
            rows={maps.map((x) => [
              x.transaction_type,
              x.tender_type || 'All',
              x.debit_account,
              x.credit_account,
              x.active ? 'Active' : 'Inactive',
            ])}
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
                {journal.total_transactions.toLocaleString()} transactions total.
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
    </SettingsSection>
  );
}
