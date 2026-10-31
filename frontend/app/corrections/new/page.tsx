'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api, money } from '@/lib/api';
import { useToast } from '@/components/Feedback';
import { Alert, Badge, Button, Card, DataTable, Field, PageHeader } from '@/components/ui';
import { SearchSelect } from '@/components/SearchSelect';
import { StoreSelect } from '@/components/StoreSelect';
import { KINDS, describe, pretty } from '@/lib/corrections';
import type { Kind } from '@/lib/corrections';

type Tx = {
  id: string;
  business_date: string;
  transaction_type: string;
  signed_amount: string;
  currency: string;
  store_id: string;
  tender_type: string | null;
  payment_reference: string | null;
  reconciliation_status: string;
};
type Facets = { stores: string[]; tenders: string[]; transaction_types: string[] };

export default function NewCorrectionPage() {
  const router = useRouter();
  const toast = useToast();
  const [error, setError] = useState<string | null>(null);
  const [facets, setFacets] = useState<Facets>({
    stores: [],
    tenders: [],
    transaction_types: [],
  });
  const [itemQuery, setItemQuery] = useState('');
  const [itemOptions, setItemOptions] = useState<{ value: string; label: string }[]>([]);
  const [kind, setKind] = useState<Kind>('Amount');
  const [search, setSearch] = useState('');
  const [results, setResults] = useState<Tx[]>([]);
  const [searching, setSearching] = useState(false);
  const [picked, setPicked] = useState<Tx | null>(null);
  const [transactionId, setTransactionId] = useState('');
  const [rationale, setRationale] = useState('');
  const [amount, setAmount] = useState('0');
  const [value, setValue] = useState('');
  const [store, setStore] = useState('');
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [tender, setTender] = useState('Cash');
  const [transactionType, setTransactionType] = useState('Sale');
  const [refundSource, setRefundSource] = useState('Processor');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get('/api/transaction-adjustments/transactions/facets')
      .then((r) => setFacets(r.data))
      .catch(() => {});
  }, []);

  useEffect(() => {
    const query = itemQuery.trim();
    if (query.length < 2) {
      setItemOptions([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      api.get('/api/foundation', { params: { category: 'Item', q: query, limit: 50 } })
        .then((response) => {
          if (!cancelled) {
            setItemOptions(response.data.items.map((item: { code: string; name: string }) => ({
              value: item.code,
              label: `${item.code} · ${item.name}`,
            })));
          }
        })
        .catch((e: any) => {
          if (!cancelled) setError(e?.response?.data?.detail ?? 'Unable to search item references.');
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [itemQuery]);

  const missing = kind === 'MissingTransaction';
  const active = KINDS.find((x) => x.key === kind)!;
  const payload = useMemo(() => {
    if (kind === 'MissingTransaction')
      return {
        business_date: date,
        store_id: store,
        amount: Number(amount),
        transaction_type: transactionType,
        ...(transactionType === 'Refund' ? { source_system: refundSource } : {}),
        tender_type: tender,
      };
    if (kind === 'Amount') return { amount_delta: Number(amount) };
    if (kind === 'Tender') return { tender_type: value };
    if (kind === 'Tax') return { tax_delta: Number(amount) };
    if (kind === 'Reference') return { reference: value };
    return { item: value };
  }, [kind, date, store, amount, transactionType, refundSource, tender, value]);
  const valid =
    !!rationale.trim() &&
    (missing ? !!store.trim() && Number(amount) > 0 : !!transactionId.trim()) &&
    (kind === 'Amount' || kind === 'Tax'
      ? Number.isFinite(Number(amount))
      : missing || !!value.trim());

  const pickKind = (k: Kind) => {
    setKind(k);
    setValue('');
    setAmount('0');
  };
  const lookup = async () => {
    if (search.trim().length < 2) return;
    try {
      setSearching(true);
      setResults(
        (
          await api.get('/api/transaction-adjustments/transactions/search', {
            params: { q: search.trim() },
          })
        ).data
      );
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Transaction search failed.');
    } finally {
      setSearching(false);
    }
  };
  const selectTransaction = (tx: Tx) => {
    setPicked(tx);
    setTransactionId(tx.id);
    setSearch('');
    setResults([]);
    if (kind === 'Tender' && tx.tender_type) setValue(tx.tender_type);
    setError(null);
  };
  const clearTransaction = () => {
    setPicked(null);
    setTransactionId('');
  };
  const submit = async () => {
    setBusy(true);
    try {
      await api.post('/api/transaction-adjustments', {
        transaction_id: missing ? null : transactionId.trim(),
        adjustment_type: kind,
        proposed_values: payload,
        rationale: rationale.trim(),
      });
      toast('Correction submitted for Finance review.', 'success');
      router.push('/corrections');
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Unable to submit correction.');
      setBusy(false);
    }
  };

  const tenders = facets.tenders.length ? facets.tenders : ['Cash', 'Card', 'GiftCard'];
  const types = facets.transaction_types.length ? facets.transaction_types : ['Sale', 'Return'];

  return (
    <div className="page-container">
      <Link href="/corrections" className="back-link">
        ← All corrections
      </Link>
      <PageHeader
        title="New correction"
        subtitle="Choose the correction, identify the source transaction, and give Finance the evidence-based rationale. The source record stays unchanged."
      />
      {error && (
        <Alert tone="error" onDismiss={() => setError(null)}>
          {error}
        </Alert>
      )}
      <div className="corr-layout">
        <div className="corr-steps">
          <Card title="1. What needs correcting?">
            <div className="ac-tiles">
              {KINDS.map((k) => (
                <button
                  key={k.key}
                  className="ac-tile"
                  aria-pressed={kind === k.key}
                  onClick={() => pickKind(k.key)}
                >
                  <b>{k.title}</b>
                  <em>{k.help}</em>
                </button>
              ))}
            </div>
          </Card>

          <Card title={missing ? '2. The missing transaction' : '2. Which transaction?'}>
            {missing ? (
              <div className="cfg-form">
                <Field label="Store">
                  <StoreSelect value={store} onChange={setStore} />
                </Field>
                <Field label="Business date">
                  <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
                </Field>
              </div>
            ) : picked || transactionId ? (
              <Alert
                tone="success"
                action={
                  <Button size="sm" onClick={clearTransaction}>
                    Change
                  </Button>
                }
              >
                {picked ? (
                  <>
                    <b>{picked.id}</b> · {picked.store_id} · {picked.business_date} ·{' '}
                    {picked.transaction_type} {money(picked.signed_amount, picked.currency)}
                    {picked.tender_type ? ` · ${picked.tender_type}` : ''}
                  </>
                ) : (
                  <>
                    Transaction <b>{transactionId}</b>
                  </>
                )}
              </Alert>
            ) : (
              <>
                <p className="hint">
                  Search by canonical ID, payment reference, settlement reference or store.
                </p>
                <div className="form-row">
                  <div className="grow">
                    <input
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') lookup();
                      }}
                      placeholder="Search transactions"
                      aria-label="Search transactions"
                    />
                  </div>
                  <Button disabled={searching || search.trim().length < 2} onClick={lookup}>
                    {searching ? 'Searching…' : 'Search'}
                  </Button>
                </div>
                {results.length > 0 && (
                  <DataTable
                    columns={[
                      {
                        key: 'tx',
                        header: 'Transaction',
                        primary: true,
                        render: (tx) => (
                          <>
                            <code style={{ fontSize: 11 }}>{tx.id}</code>
                            <small className="act-sub" style={{ display: 'block' }}>
                              {tx.payment_reference || 'No payment reference'}
                            </small>
                          </>
                        ),
                      },
                      {
                        key: 'where',
                        header: 'Store / date',
                        render: (tx) => `${tx.store_id} · ${tx.business_date}`,
                      },
                      { key: 'type', header: 'Type', render: (tx) => tx.transaction_type },
                      {
                        key: 'amt',
                        header: 'Amount',
                        numeric: true,
                        render: (tx) => money(tx.signed_amount, tx.currency),
                      },
                      { key: 'tender', header: 'Tender', render: (tx) => tx.tender_type || '—' },
                    ]}
                    rows={results}
                    rowKey={(tx) => tx.id}
                    onRowClick={selectTransaction}
                  />
                )}
                <details className="cfg-details">
                  <summary>Already have the ID? Paste it</summary>
                  <Field label="Canonical transaction ID">
                    <input
                      value={transactionId}
                      onChange={(e) => setTransactionId(e.target.value)}
                      placeholder="Paste an ID"
                    />
                  </Field>
                </details>
              </>
            )}
          </Card>

          <Card title="3. The correction">
            {(kind === 'Amount' || kind === 'Tax' || missing) && (
              <div className="cfg-form">
                <Field
                  label={missing ? 'Amount' : 'Adjustment amount'}
                  hint={
                    missing
                      ? 'Positive amount of the missing transaction.'
                      : 'Use a negative number to reduce.'
                  }
                >
                  <input
                    type="number"
                    step="0.01"
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                  />
                </Field>
                {missing && (
                  <>
                    <Field label="Transaction type">
                      <select
                        value={transactionType}
                        onChange={(e) => setTransactionType(e.target.value)}
                      >
                        {types.map((x) => (
                          <option key={x}>{x}</option>
                        ))}
                      </select>
                    </Field>
                    {transactionType === 'Refund' && (
                      <Field label="Refund source">
                        <select
                          value={refundSource}
                          onChange={(e) => setRefundSource(e.target.value)}
                        >
                          <option>Processor</option>
                          <option>Shopify</option>
                        </select>
                      </Field>
                    )}
                    <Field label="Tender">
                      <select value={tender} onChange={(e) => setTender(e.target.value)}>
                        {tenders.map((x) => (
                          <option key={x}>{x}</option>
                        ))}
                      </select>
                    </Field>
                  </>
                )}
              </div>
            )}
            {kind === 'Tender' && (
              <Field label="Correct tender">
                <SearchSelect
                  label="Correct tender"
                  value={value}
                  onChange={setValue}
                  options={tenders.map((t) => ({ value: t }))}
                  placeholder="Search tenders"
                />
              </Field>
            )}
            {kind === 'Item' && (
              <Field label="Correct item">
                <SearchSelect
                  label="Correct item"
                  value={value}
                  onChange={setValue}
                  options={itemOptions}
                  onSearch={setItemQuery}
                  placeholder="Search items"
                  allowCustom
                />
              </Field>
            )}
            {kind === 'Reference' && (
              <Field label="Correct reference">
                <input
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  placeholder="Payment or settlement reference"
                />
              </Field>
            )}
          </Card>

          <Card title="4. Why is this correction supported?">
            <Field
              label="Evidence and rationale"
              hint="Describe the receipt, source record, settlement report or other evidence you reviewed."
            >
              <textarea rows={4} value={rationale} onChange={(e) => setRationale(e.target.value)} />
            </Field>
            <div className="action-buttons">
              <Button variant="primary" disabled={!valid || busy} onClick={submit}>
                {busy ? 'Submitting…' : 'Submit for Finance review'}
              </Button>
              <Button onClick={() => router.push('/corrections')}>Cancel</Button>
            </div>
          </Card>
        </div>

        <aside className="corr-summary">
          <Card title="Your proposal">
            <Badge variant="secondary" size="sm">
              Source remains immutable
            </Badge>
            <dl className="kv">
              <dt>Type</dt>
              <dd>{pretty(kind)}</dd>
              <dt>Target</dt>
              <dd>
                {missing
                  ? 'New manual record'
                  : transactionId
                    ? picked
                      ? `${picked.store_id} · ${picked.business_date}`
                      : transactionId
                    : '—'}
              </dd>
              <dt>Effect</dt>
              <dd>{valid || missing || value || Number(amount) ? describe(kind, payload) : '—'}</dd>
              <dt>Why</dt>
              <dd>{rationale.trim() || '—'}</dd>
            </dl>
            <p className="hint">{active.help}</p>
            {missing && (
              <p className="hint">
                Approval creates an immutable manual source record and a linked canonical
                transaction.
              </p>
            )}
            <details className="cfg-details">
              <summary>Technical payload</summary>
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0 }}>
                {JSON.stringify(payload, null, 2)}
              </pre>
            </details>
          </Card>
        </aside>
      </div>
    </div>
  );
}
