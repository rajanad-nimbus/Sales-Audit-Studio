'use client';

import { Fragment, use, useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { api, money } from '@/lib/api';
import { SkeletonRows, useToast } from '@/components/Feedback';
import { StoreScopeNote } from '@/components/StoreScope';
import { CaseLink } from '@/components/MeProvider';
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  Field,
  MetricGrid,
  MetricTile,
  PageHeader,
  Segmented,
  Tabs,
  Toolbar,
} from '@/components/ui';
import { apiError, dayApi, dayStatusVariant } from '@/lib/storeDays';
import type {
  Closure,
  DayCase,
  EvidenceOverride,
  HistoryItem,
  StoreDay,
  Total,
} from '@/lib/storeDays';

type DetailTab = 'totals' | 'closure' | 'cases' | 'history';

const HISTORY_PAGE = 15;
const totalKey = (t: Total) => `${t.name}|${t.level}|${t.dimension ?? ''}`;
const hasVariance = (t: Total) => t.variance_amount !== null && Number(t.variance_amount) !== 0;
const signed = (n: number, currency: string) => `${n > 0 ? '+' : ''}${money(n, currency)}`;

export default function StoreDayDetailPage({
  params,
}: {
  params: Promise<{ storeId: string; date: string }>;
}) {
  const { storeId: rawStore, date } = use(params);
  const storeId = decodeURIComponent(rawStore);
  const toast = useToast();
  const [day, setDay] = useState<StoreDay | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [totals, setTotals] = useState<Total[]>([]);
  const [prevTotals, setPrevTotals] = useState<{ version: number; rows: Total[] } | null>(null);
  const [versions, setVersions] = useState<number[]>([]);
  const [version, setVersion] = useState<number | null>(null);
  const [blockers, setBlockers] = useState<string[]>([]);
  const [cases, setCases] = useState<DayCase[]>([]);
  const [closures, setClosures] = useState<Closure[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [overrides, setOverrides] = useState<EvidenceOverride[]>([]);
  const [rationale, setRationale] = useState('');
  const [reviewRationale, setReviewRationale] = useState('');
  const [evidenceBasis, setEvidenceBasis] = useState('');
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<DetailTab>('totals');
  const [onlyVariance, setOnlyVariance] = useState(false);
  const [historyType, setHistoryType] = useState('');
  const [historyQ, setHistoryQ] = useState('');
  const [historyLimit, setHistoryLimit] = useState(HISTORY_PAGE);

  const load = useCallback(
    async (v?: number) => {
      const ref = { store_id: storeId, business_date: date };
      try {
        const { data } = await api.get(dayApi(ref), { params: v ? { version: v } : {} });
        setDay(data.store_day);
        setTotals(data.totals);
        setVersions(data.versions);
        setVersion(data.selected_version);
        setBlockers(data.close_blockers);
        setCases(data.cases);
        setClosures(data.closure_requests);
        setHistory(data.history);
        setOverrides(data.evidence_overrides ?? []);
        setError(null);
        // Compare against the revision just before this one, so changes between versions are visible.
        const earlier = Math.max(
          0,
          ...(data.versions as number[]).filter((x) => x < data.selected_version)
        );
        setPrevTotals(null);
        if (earlier > 0) {
          try {
            const prev = await api.get(dayApi(ref), { params: { version: earlier } });
            setPrevTotals({ version: earlier, rows: prev.data.totals });
          } catch {
            /* the comparison is a nicety; the totals themselves loaded */
          }
        }
      } catch (e) {
        setError(apiError(e, 'Could not open this Store Day.'));
      } finally {
        setLoading(false);
      }
    },
    [storeId, date]
  );
  useEffect(() => {
    load();
  }, [load]);

  const act = async (path: string, body?: object, done?: () => void) => {
    setBusy(true);
    try {
      const { data } = await api.post(path, body);
      toast(
        data.transactions !== undefined
          ? `Re-totaled ${data.transactions} transaction(s); ${data.totals_created} totals created.`
          : `Store Day is now ${data.status}.`,
        'success'
      );
      done?.();
      await load();
    } catch (e) {
      toast(apiError(e, 'Action failed'), 'error');
    } finally {
      setBusy(false);
    }
  };

  const pending = closures.some((c) => c.status === 'Pending');
  const closed = day?.status === 'Closed';
  const viewingOld = !!day && version !== null && version !== day.audit_version;
  const base = day ? dayApi(day) : '';

  // Totals revision view
  const outOfBalance = totals.filter(hasVariance);
  const worst = outOfBalance.reduce<Total | null>(
    (w, t) =>
      !w || Math.abs(Number(t.variance_amount)) > Math.abs(Number(w.variance_amount)) ? t : w,
    null
  );
  const shownTotals = onlyVariance ? outOfBalance : totals;
  const prevAmounts = useMemo(
    () => new Map((prevTotals?.rows ?? []).map((t) => [totalKey(t), Number(t.calculated_amount)])),
    [prevTotals]
  );

  // History view: filter, group by day, show in pages
  const eventTypes = Array.from(new Set(history.map((h) => h.event_type))).sort();
  const filteredHistory = history
    .filter(
      (h) =>
        (!historyType || h.event_type === historyType) &&
        (!historyQ.trim() ||
          `${h.description} ${h.actor} ${h.event_type}`
            .toLowerCase()
            .includes(historyQ.trim().toLowerCase()))
    )
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  const historyByDay = filteredHistory
    .slice(0, historyLimit)
    .reduce<[string, HistoryItem[]][]>((groups, h) => {
      const label = new Date(h.created_at).toLocaleDateString(undefined, {
        weekday: 'short',
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      });
      const last = groups[groups.length - 1];
      if (last && last[0] === label) last[1].push(h);
      else groups.push([label, [h]]);
      return groups;
    }, []);

  const steps = day
    ? [
        {
          label: 'Totals',
          note: day.totals_stale ? 'Out of date' : `Version ${day.audit_version}`,
          state: day.totals_stale ? 'warn' : totals.length > 0 || closed ? 'done' : 'todo',
        },
        {
          label: 'Closure request',
          note: closed ? 'Approved' : pending ? 'Awaiting approval' : 'Not requested',
          state: closed ? 'done' : pending ? 'active' : 'todo',
        },
        { label: 'Closed', note: closed ? 'Locked' : 'Open', state: closed ? 'done' : 'todo' },
      ]
    : [];

  return (
    <div className="page-container">
      <Link href="/store-days" className="back-link">
        ← All Store Days
      </Link>
      <PageHeader
        title={`${storeId} · ${date}`}
        subtitle={day ? `Current totals version ${day.audit_version}` : undefined}
        actions={day && <Badge variant={dayStatusVariant(day.status)}>{day.status}</Badge>}
      />
      <StoreScopeNote />
      {error && (
        <Alert
          tone="error"
          action={
            <Button size="sm" className="alert-close" onClick={() => load()}>
              Retry
            </Button>
          }
        >
          {error}
        </Alert>
      )}
      {loading && <SkeletonRows rows={6} />}

      {day && (
        <>
          <ol className="sd-steps" aria-label="Store Day progress">
            {steps.map((s, i) => (
              <li
                key={s.label}
                className={`sd-step ${s.state}`}
                aria-current={s.state === 'active' ? 'step' : undefined}
              >
                <span className="sd-step-n">{s.state === 'done' ? '✓' : i + 1}</span>
                <span>
                  <b>{s.label}</b>
                  <small>{s.note}</small>
                </span>
              </li>
            ))}
          </ol>

          {day.totals_stale && (
            <Alert tone="warning">
              New data arrived after these totals were calculated.{' '}
              {closed
                ? 'Reopen the store day, then re-total it.'
                : 'Re-total the store day before closure.'}
            </Alert>
          )}
          {blockers.length > 0 && (
            <Alert tone="error">
              <b>Closure blockers:</b> {blockers.join(' · ')}
            </Alert>
          )}
          {overrides.length > 0 && (
            <Alert tone="warning">
              <div className="stack stack-sm">
                <b>
                  {overrides.length} evidence gap{overrides.length > 1 ? 's' : ''}{' '}
                  {overrides.some((o) => o.status === 'Override Pending')
                    ? 'overridden or awaiting approval'
                    : 'overridden'}{' '}
                  on this day
                </b>
                {overrides.map((o) => (
                  <span key={`${o.case_id}-${o.requirement}`}>
                    <CaseLink id={o.case_id}>{o.case_number}</CaseLink> · {o.requirement} ·{' '}
                    {o.status === 'Override Pending'
                      ? `awaiting a second approver (requested by ${o.requested_by})`
                      : `approved by ${o.approved_by}`}{' '}
                    · impact {money(o.financial_impact)}
                    {o.reason ? ` · ${o.reason}` : ''}
                  </span>
                ))}
              </div>
            </Alert>
          )}

          <div className="sd-bar">
            <Button disabled={busy || closed} onClick={() => act(`${base}/retotal`)}>
              Re-total
            </Button>
            {closed ? (
              <Button onClick={() => setTab('closure')}>Reopen…</Button>
            ) : pending ? (
              <Button
                variant="primary"
                disabled={busy || blockers.length > 0}
                title={blockers.length ? 'Resolve the blockers first' : ''}
                onClick={() => act(`${base}/close`)}
              >
                Approve &amp; close
              </Button>
            ) : (
              <Button variant="primary" onClick={() => setTab('closure')}>
                Request closure…
              </Button>
            )}
            <span className="hint">
              {closed
                ? 'This day is closed and locked.'
                : pending
                  ? 'A closure request is waiting for approval.'
                  : 'Re-total after new data arrives, then request closure.'}
            </span>
          </div>

          <Card>
            <Tabs
              value={tab}
              onChange={(id) => setTab(id as DetailTab)}
              items={[
                { id: 'totals', label: 'Totals', count: totals.length },
                { id: 'closure', label: 'Closure', count: closures.length },
                { id: 'cases', label: 'Cases', count: cases.length },
                { id: 'history', label: 'History', count: history.length },
              ]}
            />

            {tab === 'totals' && (
              <div className="sd-pane">
                <div className="sd-versions">
                  <span className="hint">Totals version</span>
                  <Segmented
                    value={String(version ?? '')}
                    onChange={(v) => load(Number(v))}
                    options={versions.map((v) => ({
                      value: String(v),
                      label: `v${v}${v === day.audit_version ? ' · current' : ''}`,
                    }))}
                  />
                </div>
                {viewingOld && (
                  <Alert
                    tone="info"
                    action={
                      <Button size="sm" onClick={() => load()}>
                        Show current
                      </Button>
                    }
                  >
                    You are looking at version {version}, an earlier calculation. The current
                    version is {day.audit_version}.
                  </Alert>
                )}
                <MetricGrid>
                  <MetricTile label="Totals" value={totals.length} />
                  <MetricTile
                    label="Out of balance"
                    value={totals.length === 0 ? '-' : outOfBalance.length}
                  />
                  <MetricTile
                    label="Largest variance"
                    value={worst ? money(worst.variance_amount ?? 0, worst.currency) : 'None'}
                  />
                </MetricGrid>
                <Toolbar
                  count={
                    prevTotals
                      ? `Changes compared with v${prevTotals.version}`
                      : versions.length > 1
                        ? undefined
                        : 'First calculation'
                  }
                >
                  <Segmented
                    value={onlyVariance ? 'variance' : 'all'}
                    onChange={(v) => setOnlyVariance(v === 'variance')}
                    options={[
                      { value: 'all', label: `All ${totals.length}` },
                      { value: 'variance', label: `Out of balance ${outOfBalance.length}` },
                    ]}
                  />
                </Toolbar>
                <DataTable
                  columns={[
                    {
                      key: 'total',
                      header: 'Total',
                      primary: true,
                      render: (t) => (
                        <>
                          {t.name}
                          <small className="act-sub" style={{ display: 'block' }}>
                            {t.level}
                            {t.dimension ? ` · ${t.dimension}` : ''}
                          </small>
                        </>
                      ),
                    },
                    {
                      key: 'calc',
                      header: 'Calculated',
                      numeric: true,
                      render: (t) => money(t.calculated_amount, t.currency),
                    },
                    ...(prevTotals
                      ? [
                          {
                            key: 'change',
                            header: `vs v${prevTotals.version}`,
                            numeric: true,
                            render: (t: Total) => {
                              const before = prevAmounts.get(totalKey(t));
                              if (before === undefined)
                                return (
                                  <Badge variant="info" size="sm">
                                    New
                                  </Badge>
                                );
                              const delta = Number(t.calculated_amount) - before;
                              return delta === 0 ? '—' : signed(delta, t.currency);
                            },
                          },
                        ]
                      : []),
                    {
                      key: 'declared',
                      header: 'Declared',
                      numeric: true,
                      render: (t) =>
                        t.declared_amount === null ? '—' : money(t.declared_amount, t.currency),
                    },
                    {
                      key: 'variance',
                      header: 'Variance',
                      numeric: true,
                      render: (t) =>
                        t.variance_amount === null ? (
                          '—'
                        ) : hasVariance(t) ? (
                          <span className="sd-neg">{money(t.variance_amount, t.currency)}</span>
                        ) : (
                          <Badge variant="success" size="sm">
                            Balanced
                          </Badge>
                        ),
                    },
                  ]}
                  rows={shownTotals}
                  rowKey={(t) => t.id}
                  rowClassName={(t) => (hasVariance(t) ? 'sd-row-bad' : '')}
                  emptyTitle={
                    totals.length === 0
                      ? 'No totals for this version'
                      : 'Everything balances in this version'
                  }
                  emptyHint={
                    totals.length === 0 ? 'Re-total the store day to calculate them.' : undefined
                  }
                />
              </div>
            )}

            {tab === 'closure' && (
              <div className="sd-pane">
                {closed ? (
                  <div className="sd-section">
                    <h3>Reopen this Store Day</h3>
                    <p className="hint">
                      Reopening unlocks the day so it can be re-totaled. A reason is required and is
                      written to the history.
                    </p>
                    <div className="form-row">
                      <div className="grow">
                        <Field label="Reopen rationale">
                          <input
                            value={rationale}
                            onChange={(e) => setRationale(e.target.value)}
                            placeholder="Why does it need to reopen?"
                          />
                        </Field>
                      </div>
                      <Button
                        disabled={busy || !rationale.trim()}
                        onClick={() => act(`${base}/reopen`, { rationale }, () => setRationale(''))}
                      >
                        Reopen
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="sd-section">
                    <h3>Request closure</h3>
                    <p className="hint">
                      State the evidence that the totals are right. A second person approves the
                      closure.
                    </p>
                    <div className="form-row">
                      <div className="grow">
                        <Field label="Evidence basis">
                          <input
                            value={evidenceBasis}
                            onChange={(e) => setEvidenceBasis(e.target.value)}
                            placeholder="For example: variances explained in the linked cases"
                          />
                        </Field>
                      </div>
                      <Button
                        variant="primary"
                        disabled={busy || blockers.length > 0 || !evidenceBasis.trim()}
                        onClick={() =>
                          act(`${base}/closure-requests`, { evidence_basis: evidenceBasis }, () =>
                            setEvidenceBasis('')
                          )
                        }
                      >
                        Request closure
                      </Button>
                    </div>
                    {blockers.length > 0 && (
                      <p className="hint">Resolve the closure blockers above first.</p>
                    )}
                  </div>
                )}

                <div className="sd-section">
                  <h3>Closure requests ({closures.length})</h3>
                  {closures.length === 0 ? (
                    <p className="cc-empty">No closure requests yet.</p>
                  ) : (
                    closures.map((c) => (
                      <div className="case-note" key={c.id}>
                        <Badge variant={dayStatusVariant(c.status)} size="sm">
                          {c.status}
                        </Badge>{' '}
                        <span>
                          {c.requested_by} · {new Date(c.created_at).toLocaleString()}
                        </span>
                        <p>{c.evidence_basis}</p>
                        {c.review_reason && <p>Review: {c.review_reason}</p>}
                        {c.status === 'Pending' && (
                          <div className="form-row">
                            <div className="grow">
                              <Field label="Rejection rationale">
                                <input
                                  value={reviewRationale}
                                  onChange={(e) => setReviewRationale(e.target.value)}
                                  placeholder="Required to reject"
                                />
                              </Field>
                            </div>
                            <Button
                              disabled={busy || !reviewRationale.trim()}
                              onClick={() =>
                                act(
                                  `${base}/closure-requests/${c.id}/reject`,
                                  { rationale: reviewRationale },
                                  () => setReviewRationale('')
                                )
                              }
                            >
                              Reject request
                            </Button>
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </div>
            )}

            {tab === 'cases' && (
              <div className="sd-pane">
                <DataTable
                  columns={[
                    {
                      key: 'case',
                      header: 'Case',
                      primary: true,
                      render: (c) => <CaseLink id={c.id}>{c.case_number}</CaseLink>,
                    },
                    { key: 'type', header: 'Type', render: (c) => c.case_type },
                    {
                      key: 'status',
                      header: 'Status',
                      render: (c) => (
                        <Badge variant="secondary" size="sm">
                          {c.status}
                        </Badge>
                      ),
                    },
                    {
                      key: 'exposure',
                      header: 'Exposure',
                      numeric: true,
                      render: (c) => money(c.exposure),
                    },
                  ]}
                  rows={cases}
                  rowKey={(c) => c.id}
                  emptyTitle="No cases for this Store Day"
                />
              </div>
            )}

            {tab === 'history' && (
              <div className="sd-pane">
                {history.length === 0 ? (
                  <p className="cc-empty">No history yet.</p>
                ) : (
                  <>
                    <Toolbar count={`${filteredHistory.length} of ${history.length} events`}>
                      <input
                        className="grow"
                        type="search"
                        value={historyQ}
                        onChange={(e) => {
                          setHistoryQ(e.target.value);
                          setHistoryLimit(HISTORY_PAGE);
                        }}
                        placeholder="Search history"
                        aria-label="Search history"
                      />
                      <select
                        aria-label="Filter by event"
                        value={historyType}
                        onChange={(e) => {
                          setHistoryType(e.target.value);
                          setHistoryLimit(HISTORY_PAGE);
                        }}
                      >
                        <option value="">All events</option>
                        {eventTypes.map((t) => (
                          <option key={t} value={t}>
                            {t.replaceAll('_', ' ')}
                          </option>
                        ))}
                      </select>
                    </Toolbar>
                    {filteredHistory.length === 0 ? (
                      <p className="cc-empty">No events match.</p>
                    ) : (
                      historyByDay.map(([label, items]) => (
                        <Fragment key={label}>
                          <h4 className="sd-day">{label}</h4>
                          <ol className="case-timeline">
                            {items.map((h) => (
                              <li key={h.id}>
                                <time>
                                  {new Date(h.created_at).toLocaleTimeString([], {
                                    hour: '2-digit',
                                    minute: '2-digit',
                                  })}
                                </time>
                                <div>
                                  <b>{h.event_type.replaceAll('_', ' ')}</b>
                                  <p>{h.description}</p>
                                  <small>{h.actor}</small>
                                </div>
                              </li>
                            ))}
                          </ol>
                        </Fragment>
                      ))
                    )}
                    {filteredHistory.length > historyLimit && (
                      <div>
                        <Button onClick={() => setHistoryLimit(historyLimit + HISTORY_PAGE)}>
                          Show {Math.min(HISTORY_PAGE, filteredHistory.length - historyLimit)} more
                        </Button>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
