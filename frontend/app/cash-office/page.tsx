'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, money } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { StoreScopeNote } from '@/components/StoreScope';
import { useMe } from '@/components/MeProvider';
import { Alert, Button, DataTable, PageNav } from '@/components/ui';
import { StoreSelect } from '@/components/StoreSelect';
import { LookupSelect } from '@/components/LookupSelect';
import { OverShortReport } from '@/components/OverShortReport';
import { VoucherLedger } from '@/components/VoucherLedger';
type Cash = {
  id: string;
  store_id: string;
  business_date: string;
  register_id: string | null;
  cashier_id: string | null;
  tender_type: string;
  declared_cash: string;
  paid_out: string;
  safe_drop: string;
  expected_amount: string | null;
  variance_amount: string | null;
  status: string;
};
type Deposit = {
  id: string;
  store_id: string;
  business_date: string;
  deposit_reference: string;
  bank_account: string | null;
  deposited_amount: string;
  expected_amount: string | null;
  variance_amount: string | null;
  status: string;
};
const validDate = (value: string | null) => {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return '';
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value ? value : '';
};
export default function CashOfficePage() {
  const { me } = useMe();
  const [rows, setRows] = useState<Cash[]>([]),
    [deposits, setDeposits] = useState<Deposit[]>([]),
    [controlTotal, setControlTotal] = useState(0),
    [depositTotal, setDepositTotal] = useState(0),
    [notice, setNotice] = useState<string | null>(null);
  const [historyStore, setHistoryStore] = useState('');
  const [historyStart, setHistoryStart] = useState('');
  const [historyEnd, setHistoryEnd] = useState('');
  const [controlsPage, setControlsPage] = useState(1);
  const [depositsPage, setDepositsPage] = useState(1);
  const [historyReady, setHistoryReady] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [controlLoadError, setControlLoadError] = useState<string | null>(null);
  const [depositLoadError, setDepositLoadError] = useState<string | null>(null);
  const historyRequestId = useRef(0);
  const retryRequestIds = useRef({ controls: 0, deposits: 0 });
  const [exporting, setExporting] = useState<'controls' | 'deposits' | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [store, setStore] = useState(''),
    [date, setDate] = useState(new Date().toISOString().slice(0, 10)),
    [register, setRegister] = useState(''),
    [cashier, setCashier] = useState(''),
    [tender, setTender] = useState('Cash'),
    [declared, setDeclared] = useState('0'),
    [paidOut, setPaidOut] = useState('0'),
    [safeDrop, setSafeDrop] = useState('0'),
    [depositRef, setDepositRef] = useState(''),
    [depositAmount, setDepositAmount] = useState('0'),
    [bankAccount, setBankAccount] = useState('');
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setHistoryStore(params.get('cash_store') ?? '');
    const legacyDate = validDate(params.get('cash_date'));
    const restoredStart = validDate(params.get('cash_start')) || legacyDate;
    const restoredEnd = validDate(params.get('cash_end')) || legacyDate;
    setHistoryStart(restoredStart && restoredEnd && restoredStart > restoredEnd ? restoredEnd : restoredStart);
    setHistoryEnd(restoredStart && restoredEnd && restoredStart > restoredEnd ? restoredStart : restoredEnd);
    const controls = Number(params.get('cash_controls_page'));
    const deposits = Number(params.get('cash_deposits_page'));
    if (Number.isInteger(controls) && controls > 0 && controls <= 100_000) setControlsPage(controls);
    if (Number.isInteger(deposits) && deposits > 0 && deposits <= 100_000) setDepositsPage(deposits);
    setHistoryReady(true);
  }, []);
  useEffect(() => {
    if (!historyReady) return;
    const url = new URL(window.location.href);
    const values: Record<string, string> = {
      cash_store: historyStore,
      cash_start: historyStart,
      cash_end: historyEnd,
      cash_controls_page: String(controlsPage),
      cash_deposits_page: String(depositsPage),
    };
    for (const [key, value] of Object.entries(values)) {
      if (value && value !== '1') url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    }
    url.searchParams.delete('cash_date');
    window.history.replaceState(null, '', url);
  }, [historyReady, historyStore, historyStart, historyEnd, controlsPage, depositsPage]);
  useEffect(() => {
    if (!historyReady) return;
    if (me?.store_scoped && me.stores?.length === 1 && !store) setStore(me.stores[0]);
  }, [me, historyReady]);
  const load = useCallback(async () => {
    const requestId = ++historyRequestId.current;
    setHistoryLoading(true);
    setRows([]);
    setDeposits([]);
    setControlTotal(0);
    setDepositTotal(0);
    setNotice(null);
    setControlLoadError(null);
    setDepositLoadError(null);
    try {
      const filters = {
        store_id: historyStore || undefined,
        start_date: historyStart || undefined,
        end_date: historyEnd || undefined,
      };
      const [controlsResult, depositsResult] = await Promise.allSettled([
        api.get('/api/cash-controls', { params: { ...filters, skip: (controlsPage - 1) * 100, limit: 100 } }),
        api.get('/api/cash-controls/deposits', {
          params: { ...filters, skip: (depositsPage - 1) * 100, limit: 100 },
        }),
      ]);
      if (requestId !== historyRequestId.current) return;
      if (controlsResult.status === 'fulfilled') {
        setRows(controlsResult.value.data);
        setControlTotal(Number(controlsResult.value.headers['x-total-count'] ?? controlsResult.value.data.length));
      } else {
        setControlLoadError('Unable to load cash controls. Try again or check your access.');
      }
      if (depositsResult.status === 'fulfilled') {
        setDeposits(depositsResult.value.data);
        setDepositTotal(Number(depositsResult.value.headers['x-total-count'] ?? depositsResult.value.data.length));
      } else {
        setDepositLoadError('Unable to load banked deposits. Try again or check your access.');
      }
    } catch {
      if (requestId !== historyRequestId.current) return;
      setControlLoadError('Unable to load cash controls. Try again or check your access.');
      setDepositLoadError('Unable to load banked deposits. Try again or check your access.');
    } finally {
      if (requestId === historyRequestId.current) setHistoryLoading(false);
    }
  }, [historyStore, historyStart, historyEnd, controlsPage, depositsPage]);
  const retryHistory = async (kind: 'controls' | 'deposits') => {
    const requestId = historyRequestId.current;
    const retryId = ++retryRequestIds.current[kind];
    const path = kind === 'controls' ? '/api/cash-controls' : '/api/cash-controls/deposits';
    const page = kind === 'controls' ? controlsPage : depositsPage;
    try {
      const { data, headers } = await api.get(path, {
        params: {
          store_id: historyStore || undefined,
          start_date: historyStart || undefined,
          end_date: historyEnd || undefined,
          skip: (page - 1) * 100,
          limit: 100,
        },
      });
      if (requestId !== historyRequestId.current || retryId !== retryRequestIds.current[kind]) return;
      if (kind === 'controls') {
        setRows(data);
        setControlTotal(Number(headers['x-total-count'] ?? data.length));
        setControlLoadError(null);
      } else {
        setDeposits(data);
        setDepositTotal(Number(headers['x-total-count'] ?? data.length));
        setDepositLoadError(null);
      }
    } catch {
      if (requestId !== historyRequestId.current || retryId !== retryRequestIds.current[kind]) return;
      if (kind === 'controls') setControlLoadError('Unable to load cash controls. Try again or check your access.');
      else setDepositLoadError('Unable to load banked deposits. Try again or check your access.');
    }
  };
  useEffect(() => {
    if (!historyReady) return;
    load();
  }, [load, historyReady]);
  const exportHistory = async (kind: 'controls' | 'deposits') => {
    setExporting(kind);
    setExportError(null);
    try {
      const path = kind === 'controls' ? '/api/cash-controls' : '/api/cash-controls/deposits';
      const { data } = await api.get(path, {
        params: {
          store_id: historyStore || undefined,
          start_date: historyStart || undefined,
          end_date: historyEnd || undefined,
          format: 'csv',
        },
        responseType: 'blob',
      });
      downloadBlob(kind === 'controls' ? 'cash-controls.csv' : 'cash-deposits.csv', data);
    } catch (e) {
      const statusCode = (e as { response?: { status?: number } }).response?.status;
      setExportError(
        statusCode === 413
          ? 'This export exceeds 50,000 rows. Filter by store or business date and try again.'
          : 'Could not download the filtered Cash Office history.'
      );
    } finally {
      setExporting(null);
    }
  };
  const copyHistoryLink = async () => {
    try {
      const url = new URL(window.location.href);
      const values: Record<string, string> = {
        cash_store: historyStore,
        cash_start: historyStart,
        cash_end: historyEnd,
        cash_controls_page: String(controlsPage),
        cash_deposits_page: String(depositsPage),
      };
      for (const [key, value] of Object.entries(values)) {
        if (value && value !== '1') url.searchParams.set(key, value);
        else url.searchParams.delete(key);
      }
      url.searchParams.delete('cash_date');
      await navigator.clipboard.writeText(url.toString());
      setNotice('Cash history link copied.');
    } catch {
      setNotice('Could not copy the link. You can copy it from the address bar.');
    }
  };
  const submit = async () => {
    try {
      const { data } = await api.post('/api/cash-controls', {
        store_id: store,
        business_date: date,
        register_id: register || null,
        cashier_id: cashier || null,
        tender_type: tender,
        declared_cash: declared,
        paid_out: paidOut,
        safe_drop: safeDrop,
      });
      setNotice(`${data.status}: drawer variance ${money(data.variance_amount)}`);
      await load();
    } catch (e: any) {
      setNotice(e?.response?.data?.detail ?? 'Cash declaration failed');
    }
  };
  const recordDeposit = async () => {
    try {
      const { data } = await api.post('/api/cash-controls/deposits', {
        store_id: store,
        business_date: date,
        deposit_reference: depositRef,
        deposited_amount: depositAmount,
        bank_account: bankAccount || null,
      });
      setNotice(`${data.status}: deposit variance ${money(data.variance_amount)}`);
      setDepositRef('');
      await load();
    } catch (e: any) {
      setNotice(e?.response?.data?.detail ?? 'Deposit recording failed');
    }
  };
  return (
    <div className="cc">
      <div className="cc-top">
        <div>
          <h1>Cash Office</h1>
          <span className="cc-live">
            Drawer declarations, safe drops, banked deposits, and over/short review
          </span>
        </div>
      </div>
      <StoreScopeNote />
      {notice && <Alert tone="info">{notice}</Alert>}
      <div className="cc-card">
        <h3>Declare cash</h3>
        <div className="profile-form">
          <StoreSelect value={store} onChange={setStore} />
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          <LookupSelect kind="registers" value={register} onChange={setRegister} storeId={store} />
          <LookupSelect kind="cashiers" value={cashier} onChange={setCashier} storeId={store} />
          <select value={tender} onChange={(e) => setTender(e.target.value)}>
            <option>Cash</option>
            <option>Card</option>
            <option>GiftCard</option>
          </select>
          <input
            value={declared}
            onChange={(e) => setDeclared(e.target.value)}
            placeholder="Declared cash"
          />
          <input
            value={paidOut}
            onChange={(e) => setPaidOut(e.target.value)}
            placeholder="Paid-out"
          />
          <input
            value={safeDrop}
            onChange={(e) => setSafeDrop(e.target.value)}
            placeholder="Safe drop"
          />
          <Button variant="primary" size="sm" disabled={!store} onClick={submit}>
            Calculate control
          </Button>
        </div>
      </div>
      <div className="cc-card">
        <h3>Record banked deposit</h3>
        <p className="page-sub">
          The deposit is reconciled against Cash declarations and safe drops for the same store day.
        </p>
        <div className="profile-form">
          <input
            value={depositRef}
            onChange={(e) => setDepositRef(e.target.value)}
            placeholder="Bank deposit reference"
          />
          <input
            value={depositAmount}
            onChange={(e) => setDepositAmount(e.target.value)}
            placeholder="Deposited amount"
          />
          <input
            value={bankAccount}
            onChange={(e) => setBankAccount(e.target.value)}
            placeholder="Bank account (optional)"
          />
          <Button
            variant="primary"
            size="sm"
            disabled={!store || !depositRef}

            onClick={recordDeposit}
          >
            Record deposit
          </Button>
        </div>
      </div>
      <div className="cc-card">
        <h3>History filters</h3>
        <p className="page-sub">Apply the same store and business date range to both cash histories.</p>
        <Button variant="secondary" size="sm" onClick={() => void copyHistoryLink()}>
          Copy history link
        </Button>
        <div className="profile-form">
          <StoreSelect
            value={historyStore}
            onChange={(value) => {
              setHistoryStore(value);
              setControlsPage(1);
              setDepositsPage(1);
            }}
            emptyLabel="All stores"
          />
          <input
            type="date"
            value={historyStart}
            max={historyEnd || undefined}
            aria-label="Cash history from business date"
            onChange={(event) => {
              setHistoryStart(event.target.value);
              setControlsPage(1);
              setDepositsPage(1);
            }}
          />
          <input
            type="date"
            value={historyEnd}
            min={historyStart || undefined}
            aria-label="Cash history to business date"
            onChange={(event) => {
              setHistoryEnd(event.target.value);
              setControlsPage(1);
              setDepositsPage(1);
            }}
          />
        </div>
        {exportError && <Alert tone="error">{exportError}</Alert>}
      </div>
      <div className="cc-card">
        <div className="cc-history-heading">
          <h3>Cash controls</h3>
          <Button
            size="sm"
            disabled={historyLoading || controlTotal === 0 || exporting !== null}
            onClick={() => exportHistory('controls')}
          >
            {exporting === 'controls' ? 'Preparing…' : 'Download matching CSV'}
          </Button>
        </div>
        {controlLoadError && (
          <Alert tone="error" action={<Button size="sm" onClick={() => void retryHistory('controls')}>Retry</Button>}>
            {controlLoadError}
          </Alert>
        )}
        <Data
          headers={[
            'Store / date',
            'Register',
            'Tender',
            'Expected',
            'Accounted',
            'Variance',
            'Status',
          ]}
          rows={rows.map((r) => [
            `${r.store_id} · ${r.business_date}`,
            r.register_id || '—',
            r.tender_type,
            r.expected_amount === null ? '—' : money(r.expected_amount),
            money(Number(r.declared_cash) + Number(r.paid_out) + Number(r.safe_drop)),
            r.variance_amount === null ? '—' : money(r.variance_amount),
            r.status,
          ])}
          csvFileName="cash-controls-visible.csv"
          detachTitle={`Cash controls · ${historyStore || 'all stores'} · ${historyStart || 'any date'} to ${historyEnd || 'latest'} · page ${controlsPage}`}
        />
        <p className="page-sub">
          Showing {rows.length ? (controlsPage - 1) * 100 + 1 : 0}–{(controlsPage - 1) * 100 + rows.length} of {controlTotal} cash controls.
        </p>
        {controlTotal > 100 && (
          <PageNav
            page={controlsPage}
            hasMore={controlsPage * 100 < controlTotal}
            onPage={setControlsPage}
          />
        )}
      </div>
      <OverShortReport />
      <VoucherLedger />
      <div className="cc-card">
        <div className="cc-history-heading">
          <h3>Banked deposits</h3>
          <Button
            size="sm"
            disabled={historyLoading || depositTotal === 0 || exporting !== null}
            onClick={() => exportHistory('deposits')}
          >
            {exporting === 'deposits' ? 'Preparing…' : 'Download matching CSV'}
          </Button>
        </div>
        {depositLoadError && (
          <Alert tone="error" action={<Button size="sm" onClick={() => void retryHistory('deposits')}>Retry</Button>}>
            {depositLoadError}
          </Alert>
        )}
        <Data
          headers={[
            'Store / date',
            'Deposit reference',
            'Expected banked',
            'Deposited',
            'Variance',
            'Status',
          ]}
          rows={deposits.map((d) => [
            `${d.store_id} · ${d.business_date}`,
            d.deposit_reference,
            d.expected_amount === null ? '—' : money(d.expected_amount),
            money(d.deposited_amount),
            d.variance_amount === null ? '—' : money(d.variance_amount),
            d.status,
          ])}
          csvFileName="cash-deposits-visible.csv"
          detachTitle={`Banked deposits · ${historyStore || 'all stores'} · ${historyStart || 'any date'} to ${historyEnd || 'latest'} · page ${depositsPage}`}
        />
        <p className="page-sub">
          Showing {deposits.length ? (depositsPage - 1) * 100 + 1 : 0}–{(depositsPage - 1) * 100 + deposits.length} of {depositTotal} deposits.
        </p>
        {depositTotal > 100 && (
          <PageNav
            page={depositsPage}
            hasMore={depositsPage * 100 < depositTotal}
            onPage={setDepositsPage}
          />
        )}
      </div>
    </div>
  );
}
function Data({
  headers,
  rows,
  csvFileName,
  detachTitle,
}: {
  headers: string[];
  rows: string[][];
  csvFileName: string;
  detachTitle: string;
}) {
  return (
    <DataTable
      columns={headers.map((header, i) => ({
        key: header,
        header,
        csvValue: (row: string[]) => row[i],
        render: (row: string[]) => row[i],
      }))}
      rows={rows}
      rowKey={(r) => r.join('|')}
      emptyTitle="No records yet."
      csvFileName={csvFileName}
      detachTitle={detachTitle}
    />
  );
}
