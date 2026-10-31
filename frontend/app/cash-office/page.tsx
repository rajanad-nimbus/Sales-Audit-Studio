'use client';
import { useCallback, useEffect, useState } from 'react';
import { api, money } from '@/lib/api';
import { StoreScopeNote } from '@/components/StoreScope';
import { useMe } from '@/components/MeProvider';
import { Alert, Button, DataTable } from '@/components/ui';
import { StoreSelect } from '@/components/StoreSelect';
import { LookupSelect } from '@/components/LookupSelect';
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
export default function CashOfficePage() {
  const { me } = useMe();
  const [rows, setRows] = useState<Cash[]>([]),
    [deposits, setDeposits] = useState<Deposit[]>([]),
    [controlTotal, setControlTotal] = useState(0),
    [depositTotal, setDepositTotal] = useState(0),
    [notice, setNotice] = useState<string | null>(null);
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
    if (me?.store_scoped && me.stores?.length === 1 && !store) setStore(me.stores[0]);
  }, [me]);
  const load = useCallback(async () => {
    try {
      const [a, b] = await Promise.all([
        api.get('/api/cash-controls', { params: { skip: 0, limit: 100 } }),
        api.get('/api/cash-controls/deposits', { params: { skip: 0, limit: 100 } }),
      ]);
      setRows(a.data);
      setDeposits(b.data);
      setControlTotal(Number(a.headers['x-total-count'] ?? a.data.length));
      setDepositTotal(Number(b.headers['x-total-count'] ?? b.data.length));
    } catch {
      setNotice('Unable to load cash controls. Sign in with a Finance or IT role.');
    }
  }, []);
  const loadMoreControls = async () => {
    try {
      const { data } = await api.get('/api/cash-controls', {
        params: { skip: rows.length, limit: 100 },
      });
      setRows((current) => [...current, ...data]);
    } catch {
      setNotice('Unable to load more cash controls.');
    }
  };
  const loadMoreDeposits = async () => {
    try {
      const { data } = await api.get('/api/cash-controls/deposits', {
        params: { skip: deposits.length, limit: 100 },
      });
      setDeposits((current) => [...current, ...data]);
    } catch {
      setNotice('Unable to load more deposits.');
    }
  };
  useEffect(() => {
    load();
  }, [load]);
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
        <h3>Cash controls</h3>
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
        />
        <p className="page-sub">
          Showing {rows.length} of {controlTotal} cash controls.
        </p>
        {rows.length < controlTotal && (
          <Button variant="secondary" size="sm" onClick={loadMoreControls}>
            Load more
          </Button>
        )}
      </div>
      <div className="cc-card">
        <h3>Banked deposits</h3>
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
        />
        <p className="page-sub">
          Showing {deposits.length} of {depositTotal} deposits.
        </p>
        {deposits.length < depositTotal && (
          <Button variant="secondary" size="sm" onClick={loadMoreDeposits}>
            Load more
          </Button>
        )}
      </div>
    </div>
  );
}
function Data({ headers, rows }: { headers: string[]; rows: string[][] }) {
  return (
    <DataTable
      columns={headers.map((header, i) => ({ key: header, header, render: (r: string[]) => r[i] }))}
      rows={rows}
      rowKey={(r) => r.join('|')}
      emptyTitle="No records yet."
    />
  );
}
