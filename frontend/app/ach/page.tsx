'use client';

import { FormEvent, useCallback, useEffect, useState } from 'react';
import { api, money } from '@/lib/api';
import { useMe } from '@/components/MeProvider';
import { StoreSelect } from '@/components/StoreSelect';
import { Alert, Button, Card, PageHeader, Tabs } from '@/components/ui';

type RecordType = 'Bank' | 'Store';
interface Adjustment {
  id: string;
  record_type: RecordType;
  store_id: string | null;
  bank_id: string;
  account_reference: string | null;
  business_date: string;
  currency: string;
  adjustment_amount: string;
  note: string;
  status: 'Active' | 'Reversed';
  created_by: string;
  created_at: string;
}
interface AdjustmentPage {
  items: Adjustment[];
  has_more: boolean;
}
interface SettlementRow {
  bank_id: string;
  store_id: string | null;
  account_reference: string | null;
  currency: string;
  net_adjustment: string | number;
  active_adjustments: number;
}

const today = () => new Date().toISOString().slice(0, 10);

export default function ACHPage() {
  const { me } = useMe();
  const [initialQuery] = useState(() => {
    const query =
      typeof window === 'undefined' ? null : new URLSearchParams(window.location.search);
    return {
      recordType:
        me?.store_scoped || query?.get('record_type') === 'Store'
          ? ('Store' as const)
          : ('Bank' as const),
      store: query?.get('store_id') ?? '',
      businessDate: query?.get('business_date') ?? today(),
      filterDate: query?.get('business_date') ?? today(),
      filterBank: query?.get('bank_id') ?? '',
    };
  });
  const [recordType, setRecordType] = useState<RecordType>(initialQuery.recordType);
  const [store, setStore] = useState(initialQuery.store);
  const [bank, setBank] = useState('');
  const [account, setAccount] = useState('');
  const [businessDate, setBusinessDate] = useState(initialQuery.businessDate);
  const [currency, setCurrency] = useState('USD');
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const [filterStore, setFilterStore] = useState(initialQuery.store);
  const [filterBank, setFilterBank] = useState(initialQuery.filterBank);
  const [filterDate, setFilterDate] = useState(initialQuery.filterDate);
  const [filterStatus, setFilterStatus] = useState<'' | 'Active' | 'Reversed'>('');
  const [offset, setOffset] = useState(0);
  const [result, setResult] = useState<AdjustmentPage | null>(null);
  const [settlementRows, setSettlementRows] = useState<SettlementRow[]>([]);
  const [settlementDate, setSettlementDate] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const canWrite = me?.role === 'admin' || me?.role === 'finance';

  useEffect(() => {
    if (me?.store_scoped && recordType !== 'Store') {
      setRecordType('Store');
      setFilterBank('');
    }
  }, [me?.store_scoped, recordType]);

  const load = useCallback(
    async (nextOffset = 0) => {
      setBusy(true);
      try {
        const [adjustments, summary] = await Promise.all([
          api.get<AdjustmentPage>('/api/ach/adjustments', {
            params: {
              record_type: recordType,
              store_id: recordType === 'Store' ? filterStore || undefined : undefined,
              bank_id: recordType === 'Bank' ? filterBank || undefined : undefined,
              business_date: filterDate || undefined,
              status: filterStatus || undefined,
              skip: nextOffset,
              limit: 100,
            },
          }),
          filterDate
            ? api.get<{ settlement_date: string; items: SettlementRow[] }>(
                '/api/ach/settlement-summary',
                {
                  params: {
                    record_type: recordType,
                    store_id: recordType === 'Store' ? filterStore || undefined : undefined,
                    bank_id: recordType === 'Bank' ? filterBank || undefined : undefined,
                    business_date: filterDate,
                  },
                }
              )
            : Promise.resolve({ data: { settlement_date: '', items: [] as SettlementRow[] } }),
        ]);
        setResult(adjustments.data);
        setSettlementRows(summary.data.items);
        setSettlementDate(summary.data.settlement_date);
        setOffset(nextOffset);
        setError('');
      } catch (e: unknown) {
        const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
        setError(detail || 'ACH adjustments could not be loaded.');
        setResult(null);
        setSettlementRows([]);
      } finally {
        setBusy(false);
      }
    },
    [filterBank, filterDate, filterStatus, filterStore, recordType]
  );

  useEffect(() => {
    void load(0);
  }, [load]);

  async function addAdjustment(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await api.post('/api/ach/adjustments', {
        record_type: recordType,
        store_id: recordType === 'Store' ? store : null,
        bank_id: bank,
        account_reference: account || null,
        business_date: businessDate,
        currency,
        adjustment_amount: amount,
        note,
      });
      setMessage(`${recordType} ACH adjustment recorded.`);
      setAmount('');
      setNote('');
      await load(0);
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      setError(detail || 'ACH adjustment could not be saved.');
    } finally {
      setBusy(false);
    }
  }

  async function reverse(row: Adjustment) {
    const reason = window.prompt(`Reason for reversing adjustment ${row.id.slice(0, 8)}:`);
    if (!reason?.trim()) return;
    setBusy(true);
    try {
      await api.post(`/api/ach/adjustments/${encodeURIComponent(row.id)}/reverse`, null, {
        params: { reason: reason.trim() },
      });
      setMessage('ACH adjustment reversed and retained in the audit history.');
      await load(offset);
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      setError(detail || 'ACH adjustment could not be reversed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="page-container">
      <PageHeader
        title="ACH Maintenance"
        subtitle="Review next-day bank and store deposit adjustments. Each change is retained in the audit trail."
      />
      <Tabs
        value={recordType}
        onChange={(value) => setRecordType(value as RecordType)}
        items={[
          ...(!me?.store_scoped ? [{ id: 'Bank', label: 'Bank ACH' }] : []),
          { id: 'Store', label: 'Store ACH' },
        ]}
      />
      {error && <Alert tone="error">{error}</Alert>}
      {message && (
        <Alert tone="success" onDismiss={() => setMessage('')}>
          {message}
        </Alert>
      )}

      {canWrite && (
        <Card>
          <h2>Add {recordType.toLowerCase()} ACH adjustment</h2>
          <p className="hint">
            Enter a positive or negative amount. Account reference should be a masked value or
            internal alias.
          </p>
          <form onSubmit={addAdjustment} className="profile-form">
            {recordType === 'Store' && <StoreSelect value={store} onChange={setStore} />}
            <label className="field">
              <span>Bank ID</span>
              <input
                required
                maxLength={50}
                value={bank}
                onChange={(event) => setBank(event.target.value)}
              />
            </label>
            <label className="field">
              <span>Account reference</span>
              <input
                maxLength={100}
                value={account}
                onChange={(event) => setAccount(event.target.value)}
                placeholder="Alias or last four digits"
              />
            </label>
            <label className="field">
              <span>Business day</span>
              <input
                required
                type="date"
                value={businessDate}
                onChange={(event) => setBusinessDate(event.target.value)}
              />
            </label>
            <label className="field">
              <span>Currency</span>
              <input
                required
                minLength={3}
                maxLength={3}
                value={currency}
                onChange={(event) => setCurrency(event.target.value.toUpperCase())}
              />
            </label>
            <label className="field">
              <span>Next-day adjustment</span>
              <input
                required
                type="number"
                step="0.01"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
              />
            </label>
            <label className="field">
              <span>Reason</span>
              <input
                required
                maxLength={500}
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
            </label>
            <Button
              variant="primary"
              disabled={
                busy ||
                !bank.trim() ||
                !amount ||
                !note.trim() ||
                (recordType === 'Store' && !store)
              }
            >
              Record adjustment
            </Button>
          </form>
        </Card>
      )}

      <Card>
        <div className="page-header">
          <div>
            <h2>{recordType} ACH adjustments</h2>
            <p className="hint">
              Active adjustments affect the next-day transfer. Reversed entries remain visible for
              audit.
            </p>
          </div>
          <div className="toolbar">
            {recordType === 'Store' && (
              <StoreSelect
                value={filterStore}
                onChange={(value) => {
                  setFilterStore(value);
                  setOffset(0);
                }}
                emptyLabel="All stores"
              />
            )}
            {recordType === 'Bank' && (
              <label className="field">
                <span>Bank ID</span>
                <input
                  value={filterBank}
                  onChange={(event) => {
                    setFilterBank(event.target.value);
                    setOffset(0);
                  }}
                  placeholder="All banks"
                  maxLength={50}
                />
              </label>
            )}
            <label className="field">
              <span>Business day</span>
              <input
                type="date"
                value={filterDate}
                onChange={(event) => {
                  setFilterDate(event.target.value);
                  setOffset(0);
                }}
              />
            </label>
            <label className="field">
              <span>Status</span>
              <select
                value={filterStatus}
                onChange={(event) => {
                  setFilterStatus(event.target.value as '' | 'Active' | 'Reversed');
                  setOffset(0);
                }}
              >
                <option value="">All statuses</option>
                <option value="Active">Active</option>
                <option value="Reversed">Reversed</option>
              </select>
            </label>
            <Button size="sm" disabled={busy} onClick={() => void load(0)}>
              Refresh
            </Button>
          </div>
        </div>
        {filterDate && (
          <section aria-label="Next-day settlement summary" className="ac-settlement-summary">
            <h3>Next-day manual adjustment · settlement {settlementDate || '—'}</h3>
            <p className="hint">
              Active adjustments only. This is the manual adjustment amount, not the full transfer.
            </p>
            {settlementRows.length ? (
              <div className="table-container">
                <table className="data-table">
                  <thead>
                    <tr>
                      {recordType === 'Store' && <th>Store</th>}
                      <th>Bank</th>
                      <th>Account ref.</th>
                      <th>Currency</th>
                      <th className="num">Net adjustment</th>
                      <th className="num">Entries</th>
                    </tr>
                  </thead>
                  <tbody>
                    {settlementRows.map((row) => (
                      <tr
                        key={`${row.store_id ?? ''}:${row.bank_id}:${row.account_reference ?? ''}:${row.currency}`}
                      >
                        {recordType === 'Store' && <td>{row.store_id}</td>}
                        <td>
                          {recordType === 'Store' && !me?.store_scoped ? (
                            <a
                              href={`/ach?record_type=Bank&business_date=${encodeURIComponent(filterDate)}&bank_id=${encodeURIComponent(row.bank_id)}`}
                            >
                              {row.bank_id}
                            </a>
                          ) : (
                            row.bank_id
                          )}
                        </td>
                        <td>{row.account_reference || '—'}</td>
                        <td>{row.currency}</td>
                        <td className="num">{money(row.net_adjustment, row.currency)}</td>
                        <td className="num">{row.active_adjustments}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="cc-empty">No active adjustments for this business day.</p>
            )}
          </section>
        )}
        {busy && !result ? (
          <p role="status">Loading adjustments…</p>
        ) : result?.items.length ? (
          <>
            <div className="table-container">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Business day</th>
                    {recordType === 'Store' && <th>Store</th>}
                    <th>Bank</th>
                    <th>Account ref.</th>
                    <th>Adjustment</th>
                    <th>Reason</th>
                    <th>Status</th>
                    <th>Entered by</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {result.items.map((row) => (
                    <tr key={row.id}>
                      <td>{row.business_date}</td>
                      {recordType === 'Store' && <td>{row.store_id}</td>}
                      <td>
                        {recordType === 'Store' && !me?.store_scoped ? (
                          <a
                            href={`/ach?record_type=Bank&business_date=${encodeURIComponent(row.business_date)}&bank_id=${encodeURIComponent(row.bank_id)}`}
                          >
                            Bank details · {row.bank_id}
                          </a>
                        ) : (
                          row.bank_id
                        )}
                      </td>
                      <td>{row.account_reference || '—'}</td>
                      <td className="num">{money(row.adjustment_amount, row.currency)}</td>
                      <td>{row.note}</td>
                      <td>
                        <span
                          className={`badge badge-sm ${row.status === 'Active' ? 'badge-success' : 'badge-secondary'}`}
                        >
                          {row.status}
                        </span>
                      </td>
                      <td>{row.created_by}</td>
                      <td>
                        {canWrite && row.status === 'Active' ? (
                          <Button
                            size="sm"
                            variant="secondary"
                            disabled={busy}
                            onClick={() => void reverse(row)}
                          >
                            Reverse
                          </Button>
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="ac-pager">
              <span>
                Showing {offset + 1}–{offset + result.items.length}
                {result.has_more ? '+' : ''}
              </span>
              <span className="ac-spacer" />
              <Button
                size="sm"
                disabled={busy || offset === 0}
                onClick={() => void load(Math.max(0, offset - 100))}
              >
                Previous
              </Button>
              <Button
                size="sm"
                disabled={busy || !result.has_more}
                onClick={() => void load(offset + 100)}
              >
                Next
              </Button>
            </div>
          </>
        ) : (
          <p className="cc-empty">
            No {recordType.toLowerCase()} ACH adjustments match these filters.
          </p>
        )}
      </Card>
    </main>
  );
}
