'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api, caseMoney, currencyBreakdown } from '@/lib/api';
import { formatBusinessDate, formatDateTime, formatNumber } from '@/lib/preferences';
import type { BatchStatus, CurrencyAmount } from '@/lib/api';
import { useLive } from '@/lib/sync';
import { changes, readSeen, rowMark, snapshot, writeSeen } from '@/lib/commandCenterSeen';
import type { Counter, Seen } from '@/lib/commandCenterSeen';
import { StatusBadge } from '@/components/CaseTable';
import { CaseLink, useCanOpen, useMe } from '@/components/MeProvider';
import { useToast } from '@/components/Feedback';
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  Modal,
  PageHeader,
  Skeleton,
} from '@/components/ui';
import type { BadgeVariant, Column } from '@/components/ui';

interface Failed {
  error: string;
}

interface QueueCase {
  id: string;
  case_number: string;
  case_type: string;
  store_id: string;
  currency: string | null;
  amount_needs_review: boolean;
  business_date: string;
  status: string;
  priority: string;
  exposure: string;
  close_blocking_errors?: number;
  sla_state: 'breached' | 'at_risk' | 'ok' | 'none';
  assigned_to: string | null;
}

interface WorkCounts {
  needs_decision: number;
  agent_prepared: number;
  agent_waiting_evidence: number;
  sla_breached: number;
  at_risk: number;
  unassigned: number;
  mine: number;
  snoozed: number;
  open_total: number;
  stores_affected: number;
  open_exposure: string | null;
  currency: string | null;
  amounts_by_currency: CurrencyAmount[];
  straight_through_rate: number | null;
  by_day: { date: string; count: number }[];
}

interface Hotspot {
  name: string;
  open: number;
  past_sla: number;
}

interface Work {
  counts: WorkCounts;
  queue: QueueCase[];
  mine: QueueCase[];
  hotspots: { stores: Hotspot[]; types: Hotspot[] };
}

interface StoreDays {
  business_date: string;
  expected_stores: number;
  late_total: number;
  late_stores: { store_id: string; state: string }[];
  stuck_total: number;
  stuck_truncated: boolean;
  stuck: {
    store_id: string;
    business_date: string;
    status: string;
    days_open: number;
    holding_back: string[];
  }[];
}

interface AuditErrors {
  open_total: number;
  open_types: number;
  window_days: number;
  top: { code: string; label: string; open_count: number; open_by_currency: CurrencyAmount[] }[];
}

interface FailedBatch {
  id: string;
  dataset: string;
  destination: string;
  error: string | null;
  created_at: string;
  retries: number;
}

interface Close {
  business_date: string;
  expected_stores: number;
  opened: number;
  closed: number;
  ready: number;
  blocked: number;
  unchecked: number;
  blocked_reasons: { reason: string; store_days: number }[];
  blocked_examples: { store_id: string; reasons: string[] }[];
  export: Record<string, number>;
}

/** The batch status, with the latest run (successful or not) that a rerun would repeat. */
type BatchFreshness = BatchStatus & {
  last_run?: { business_date: string; status: string } | null;
};

interface Eligible {
  count: number;
  cases: { case_id: string; case_number: string; amount: string; currency: string | null }[];
}

/** A change waiting for the person to confirm it. */
interface Pending {
  title: string;
  body: React.ReactNode;
  confirm: string;
  run: () => Promise<void>;
}

interface Exports {
  failed_batches: FailedBatch[];
  failed: number;
  pending: number;
  awaiting_ack: number;
}

interface Health {
  status: string;
  attention_total: number;
  attention: { name: string; status: string; detail: string }[];
}

interface Feeds {
  stale_after_hours: number;
  feeds: {
    source_system: string;
    state: string;
    last_accepted_at: string | null;
    received_24h: number;
    quarantined_total: number;
  }[];
  quarantine_reasons: { source_system: string; reason: string; records: number }[];
}

interface VoucherTotal {
  currency: string | null;
  vouchers: number;
  amount: string;
}

interface Cash {
  window_days: number;
  declarations: number;
  out_of_balance: number;
  short: string;
  over: string;
  vouchers_outstanding: VoucherTotal[];
  vouchers_expired: VoucherTotal[];
}

interface Activity {
  events: {
    seq: number;
    created_at: string;
    actor: string;
    event_type: string;
    description: string | null;
    case_id: string | null;
  }[];
}

interface Kpis {
  window_days: number;
  transactions: number;
  exceptions: number;
  flagged_transactions: number;
  exception_rate: number | null;
  accuracy: number | null;
  close_target_days: number;
  due_store_days: number;
  closed_on_time: number;
  timeliness: number | null;
}

interface Overview {
  generated_at: string;
  freshness: BatchFreshness | Failed | null;
  work: Work | Failed | null;
  store_days: StoreDays | Failed | null;
  audit_errors: AuditErrors | Failed | null;
  exports: Exports | Failed | null;
  health: Health | Failed | null;
  close: Close | Failed | null;
  feeds: Feeds | Failed | null;
  cash: Cash | Failed | null;
  activity: Activity | Failed | null;
  kpis?: Kpis | Failed | null;
}

type Tone = 'ok' | 'warning' | 'error' | 'quiet';

const failed = (s: unknown): s is Failed => !!s && typeof s === 'object' && 'error' in s;
/** The section's data when it loaded, otherwise null. */
const ok = <T,>(s: T | Failed | null | undefined): T | null => (s && !failed(s) ? s : null);

const REFRESH_MS = 60_000;

const TONE_COLOR: Record<Tone, string> = {
  ok: 'var(--success)',
  warning: 'var(--warning)',
  error: 'var(--error)',
  quiet: 'var(--fg-tertiary)',
};

/** Read by screen readers only, so a colored mark is never the sole signal. */
const HIDDEN: React.CSSProperties = {
  position: 'absolute',
  width: 1,
  height: 1,
  overflow: 'hidden',
  clip: 'rect(0 0 0 0)',
  whiteSpace: 'nowrap',
};

const HAIRLINE = '1px solid var(--border-light)';

const SLA: Record<string, { label: string; variant: BadgeVariant }> = {
  breached: { label: 'Breached', variant: 'error' },
  at_risk: { label: 'At risk', variant: 'warning' },
  ok: { label: 'On track', variant: 'secondary' },
};

const num = (v: string | number) => formatNumber(Number(v));
const plural = (n: number, one: string, many = `${one}s`) => `${num(n)} ${n === 1 ? one : many}`;
/** "Nov 2" for a business date (YYYY-MM-DD), read as a calendar date, not a UTC instant. */
const day = (iso: string) => formatBusinessDate(iso, { month: 'short', day: 'numeric' });
/** Time today, or date and time for anything older. */
const when = (iso: string | null) => {
  if (!iso) return 'never';
  const sameDay =
    formatDateTime(iso, { year: 'numeric', month: '2-digit', day: '2-digit' }) ===
    formatDateTime(new Date(), { year: 'numeric', month: '2-digit', day: '2-digit' });
  return sameDay
    ? formatDateTime(iso, { hour: 'numeric', minute: '2-digit' })
    : formatDateTime(iso, {
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      });
};
const voucherText = (rows: VoucherTotal[]) =>
  rows.length
    ? rows
        .map((r) => `${caseMoney(r.amount, r.currency)} in ${plural(r.vouchers, 'voucher')}`)
        .join(', ')
    : 'None';

/** The situation in one sentence: the thing a person needs to know before anything else. */
function headline(c: WorkCounts): string {
  const parts: string[] = [];
  if (c.sla_breached) parts.push(`${plural(c.sla_breached, 'case is', 'cases are')} past SLA`);
  if (c.needs_decision) parts.push(`${num(c.needs_decision)} waiting for a decision`);
  if (c.at_risk) parts.push(`${num(c.at_risk)} due within 8 hours`);
  if (!parts.length) {
    return c.open_total
      ? `Nothing is overdue. ${plural(c.open_total, 'case is', 'cases are')} open and on track.`
      : 'No open cases. Everything is clear.';
  }
  const last = parts.pop();
  return `${parts.length ? `${parts.join(', ')} and ${last}` : last}.`;
}

/* ---------- Small building blocks ---------- */

function Dot({ tone }: { tone: Tone }) {
  return (
    <span
      aria-hidden
      style={{
        width: 8,
        height: 8,
        borderRadius: 'var(--radius-pill)',
        background: TONE_COLOR[tone],
        flex: 'none',
      }}
    />
  );
}

/** A link to another screen, shown only to roles that can open it. */
function ScreenLink({ href, children }: { href: string; children: React.ReactNode }) {
  const canOpen = useCanOpen();
  if (!canOpen(href)) return null;
  return (
    <Link
      href={href}
      style={{
        fontSize: 'var(--text-sm)',
        fontWeight: 'var(--weight-medium)',
        color: 'var(--primary-text)',
      }}
    >
      {children}
    </Link>
  );
}

/** A card whose title row carries a link to the screen that owns the data. */
function Panel({
  title,
  href,
  linkLabel,
  flush,
  children,
}: {
  title: string;
  href?: string;
  linkLabel?: string;
  flush?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Card
      flush={flush}
      title={
        <span
          className="cluster"
          style={{ justifyContent: 'space-between', alignItems: 'baseline', width: '100%' }}
        >
          <span>{title}</span>
          {href && linkLabel && <ScreenLink href={href}>{linkLabel}</ScreenLink>}
        </span>
      }
    >
      {children}
    </Card>
  );
}

/** One label and value on a hairline-separated row. */
function Row({
  label,
  value,
  tone,
}: {
  label: React.ReactNode;
  value: React.ReactNode;
  tone?: Tone;
}) {
  const alert = tone === 'warning' || tone === 'error';
  return (
    <div
      className="cluster"
      style={{
        justifyContent: 'space-between',
        alignItems: 'baseline',
        padding: 'var(--space-sm) 0',
        borderTop: HAIRLINE,
        fontSize: 'var(--text-sm)',
      }}
    >
      <span style={{ color: 'var(--fg-secondary)' }}>{label}</span>
      <span
        style={{
          fontWeight: 'var(--weight-semibold)',
          fontVariantNumeric: 'tabular-nums',
          color: alert ? TONE_COLOR[tone] : 'var(--fg)',
        }}
      >
        {value}
        {alert && <span style={HIDDEN}> (needs attention)</span>}
      </span>
    </div>
  );
}

/** A headline count. It takes a status color only when the number means something is late. */
function Tile({
  label,
  value,
  note,
  tone,
  change,
  onClick,
}: {
  label: string;
  value: number;
  note?: string;
  tone: 'error' | 'warning' | 'quiet';
  /** How much the count moved since the last visit, and when that was. */
  change?: { by: number; since: string };
  onClick?: () => void;
}) {
  const hot = value > 0 && tone !== 'quiet';
  const body = (
    <>
      <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-secondary)' }}>{label}</span>
      <b
        style={{
          fontSize: 'var(--text-display)',
          lineHeight: 1.1,
          fontWeight: 'var(--weight-semibold)',
          fontVariantNumeric: 'tabular-nums',
          color: hot ? TONE_COLOR[tone] : 'var(--fg)',
        }}
      >
        {num(value)}
        {hot && <span style={HIDDEN}> (needs attention)</span>}
      </b>
      {change && change.by !== 0 && (
        <span
          style={{
            fontSize: 'var(--text-xs)',
            fontWeight: 'var(--weight-medium)',
            color:
              change.by < 0
                ? 'var(--success)'
                : tone === 'quiet'
                  ? 'var(--fg-secondary)'
                  : TONE_COLOR[tone],
          }}
        >
          {change.by > 0 ? `${num(change.by)} more` : `${num(-change.by)} fewer`} since{' '}
          {change.since}
        </span>
      )}
      {note && (
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-tertiary)' }}>{note}</span>
      )}
    </>
  );
  return onClick ? (
    <button type="button" className="cc-summary-item" onClick={onClick}>
      {body}
    </button>
  ) : (
    <div className="cc-summary-item">{body}</div>
  );
}

/* ---------- Operations status board ---------- */

interface StatusLine {
  key: string;
  title: string;
  href: string;
  tone: Tone;
  state: string;
  details?: React.ReactNode[];
}

/** One card, one row per part of the operation. Details open up only when something is wrong. */
function StatusBoard({ lines }: { lines: StatusLine[] }) {
  const canOpen = useCanOpen();
  if (!lines.length) return null;
  const attention = lines.filter((l) => l.tone === 'warning' || l.tone === 'error').length;
  return (
    <Card
      title={
        <span
          className="cluster"
          style={{ justifyContent: 'space-between', alignItems: 'baseline', width: '100%' }}
        >
          <span>Operations</span>
          <span
            style={{
              fontSize: 'var(--text-sm)',
              fontWeight: 'var(--weight-regular)',
              color: 'var(--fg-secondary)',
            }}
          >
            {attention ? `${attention} of ${lines.length} need attention` : 'All running normally'}
          </span>
        </span>
      }
    >
      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {lines.map((l, i) => (
          <li
            key={l.key}
            style={{
              padding: 'var(--space-sm) 0',
              borderTop: i ? HAIRLINE : undefined,
            }}
          >
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '8px minmax(0, 1fr) auto',
                gap: 'var(--space-sm)',
                alignItems: 'center',
              }}
            >
              <Dot tone={l.tone} />
              {canOpen(l.href) ? (
                <Link
                  href={l.href}
                  style={{
                    fontWeight: 'var(--weight-medium)',
                    color: 'var(--fg)',
                    fontSize: 'var(--text-sm)',
                  }}
                >
                  {l.title}
                </Link>
              ) : (
                <span style={{ fontWeight: 'var(--weight-medium)', fontSize: 'var(--text-sm)' }}>
                  {l.title}
                </span>
              )}
              <span
                style={{
                  fontSize: 'var(--text-sm)',
                  fontVariantNumeric: 'tabular-nums',
                  textAlign: 'right',
                  color:
                    l.tone === 'warning' || l.tone === 'error'
                      ? TONE_COLOR[l.tone]
                      : 'var(--fg-secondary)',
                  fontWeight:
                    l.tone === 'warning' || l.tone === 'error'
                      ? 'var(--weight-semibold)'
                      : 'var(--weight-regular)',
                }}
              >
                {l.state}
              </span>
            </div>
            {l.details && l.details.length > 0 && (
              <ul
                style={{
                  listStyle: 'none',
                  margin: 'var(--space-xs) 0 0',
                  padding: '0 0 0 calc(8px + var(--space-sm))',
                  display: 'grid',
                  gap: 'var(--space-xs)',
                  fontSize: 'var(--text-xs)',
                  color: 'var(--fg-secondary)',
                }}
              >
                {l.details.map((d, j) => (
                  <li key={j}>{d}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function statusLines(data: Overview, retry?: (b: FailedBatch) => void): StatusLine[] {
  const lines: StatusLine[] = [];
  const couldNotLoad = (key: string, title: string, href: string, s: Failed): StatusLine => ({
    key,
    title,
    href,
    tone: 'warning',
    state: 'Not loaded',
    details: [s.error],
  });

  if (data.store_days) {
    const s = ok(data.store_days);
    if (!s)
      lines.push(couldNotLoad('days', 'Store days', '/store-days', data.store_days as Failed));
    else
      lines.push({
        key: 'days',
        title: 'Store days',
        href: '/store-days',
        tone: s.late_total || s.stuck_total ? 'warning' : 'ok',
        state: s.late_total
          ? `${num(s.late_total)} of ${num(s.expected_stores)} late for ${day(s.business_date)}`
          : `All ${num(s.expected_stores)} in for ${day(s.business_date)}`,
        details: [
          ...(s.stuck_total
            ? [
                `${s.stuck_total}${s.stuck_truncated ? '+' : ''} open for 3 days or more:`,
                ...s.stuck.map(
                  (d) =>
                    `${d.store_id}, ${day(d.business_date)}: open ${d.days_open} days. ${d.holding_back[0] ?? ''}`
                ),
              ]
            : []),
        ],
      });
  }

  if (data.audit_errors) {
    const s = ok(data.audit_errors);
    if (!s)
      lines.push(
        couldNotLoad('errors', 'Audit errors', '/audit-errors', data.audit_errors as Failed)
      );
    else
      lines.push({
        key: 'errors',
        title: 'Audit errors',
        href: '/audit-errors',
        tone: s.open_total ? 'quiet' : 'ok',
        state: s.open_total
          ? `${num(s.open_total)} open in ${plural(s.open_types, 'type')}`
          : 'None open',
        details: s.top.slice(0, 3).map((e) => `${e.label}: ${num(e.open_count)}`),
      });
  }

  if (data.exports) {
    const s = ok(data.exports);
    if (!s) lines.push(couldNotLoad('exports', 'Exports', '/exports', data.exports as Failed));
    else {
      const waiting = [
        s.pending && `${num(s.pending)} pending`,
        s.awaiting_ack && `${num(s.awaiting_ack)} awaiting acknowledgment`,
      ].filter(Boolean) as string[];
      lines.push({
        key: 'exports',
        title: 'Exports',
        href: '/exports',
        tone: s.failed ? 'error' : 'ok',
        state: s.failed ? `${plural(s.failed, 'batch', 'batches')} failed` : 'Delivering',
        details: [
          ...(s.failed_batches ?? []).map((b) => (
            <span
              key={b.id}
              className="cluster"
              style={{
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: 'var(--space-sm)',
              }}
            >
              <span>
                {b.destination}, {b.dataset}: {b.error ?? 'delivery failed'}
                {b.retries ? ` (retried ${b.retries}x)` : ''}
              </span>
              {retry && (
                <Button size="sm" onClick={() => retry(b)}>
                  Retry
                </Button>
              )}
            </span>
          )),
          ...(waiting.length ? [waiting.join(', ')] : []),
        ],
      });
    }
  }

  if (data.health) {
    const s = ok(data.health);
    if (!s)
      lines.push(
        couldNotLoad('health', 'System health', '/diagnostics/health', data.health as Failed)
      );
    else
      lines.push({
        key: 'health',
        title: 'System health',
        href: '/diagnostics/health',
        tone: s.status === 'critical' ? 'error' : s.status === 'warning' ? 'warning' : 'ok',
        state: s.attention_total
          ? `${plural(s.attention_total, 'check needs', 'checks need')} attention`
          : 'All checks pass',
        details: s.attention.map((h) => `${h.name}: ${h.detail}`),
      });
  }

  if (data.feeds) {
    const s = ok(data.feeds);
    if (!s)
      lines.push(couldNotLoad('feeds', 'Data feeds', '/diagnostics/feeds', data.feeds as Failed));
    else {
      const behind = s.feeds.filter((f) => f.state !== 'Current');
      lines.push({
        key: 'feeds',
        title: 'Data feeds',
        href: '/diagnostics/feeds',
        tone: behind.length ? 'warning' : 'ok',
        state: behind.length
          ? `${behind.length} of ${s.feeds.length} behind`
          : `${s.feeds.length} current`,
        details: [
          ...behind.map(
            (f) =>
              `${f.source_system}: ${f.state.toLowerCase()}, last accepted ${when(f.last_accepted_at)}${
                f.quarantined_total ? `, ${num(f.quarantined_total)} held` : ''
              }`
          ),
          ...s.quarantine_reasons.map(
            (r) => `${r.source_system} held ${num(r.records)} this week: ${r.reason}`
          ),
        ],
      });
    }
  }
  return lines;
}

/**
 * The latest audit events. It takes whatever height is left beside the case tables and scrolls
 * inside, so the two columns always end level; `contain: size` keeps its own length from making
 * the row taller. Stacked on a narrow screen it keeps a fixed minimum height instead.
 */
function ActivityFeed({ data }: { data: Activity | Failed }) {
  return (
    <div style={{ flex: '1 1 auto', minHeight: 240, contain: 'size', display: 'flex' }}>
      <section
        className="card"
        aria-label="Recent activity"
        style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', margin: 0 }}
      >
        <h2 className="card-title">
          <span
            className="cluster"
            style={{ justifyContent: 'space-between', alignItems: 'baseline', width: '100%' }}
          >
            <span>Recent activity</span>
            <ScreenLink href="/audit">Open the Audit Trail</ScreenLink>
          </span>
        </h2>
        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
          {failed(data) ? (
            <p className="page-sub">{data.error}</p>
          ) : data.events.length === 0 ? (
            <p className="page-sub">No activity recorded yet.</p>
          ) : (
            <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {data.events.map((e, i) => (
                <li
                  key={e.seq}
                  style={{
                    padding: 'var(--space-sm) 0',
                    borderTop: i ? HAIRLINE : undefined,
                    fontSize: 'var(--text-sm)',
                  }}
                >
                  <div>
                    <b style={{ fontWeight: 'var(--weight-semibold)' }}>{e.actor}</b>{' '}
                    <span style={{ color: 'var(--fg-secondary)' }}>
                      {e.description ?? e.event_type}
                    </span>
                  </div>
                  <time
                    dateTime={e.created_at}
                    style={{
                      fontSize: 'var(--text-xs)',
                      color: 'var(--fg-tertiary)',
                      fontVariantNumeric: 'tabular-nums',
                    }}
                  >
                    {when(e.created_at)}
                  </time>
                </li>
              ))}
            </ol>
          )}
        </div>
      </section>
    </div>
  );
}

/* ---------- Close readiness ---------- */

const EXPORT_ORDER = ['Final', 'Exported', 'In progress', 'Not exported'];
const EXPORT_LABEL: Record<string, string> = {
  Final: 'Acknowledged',
  Exported: 'Sent, awaiting acknowledgment',
  'In progress': 'Sending',
  'Not exported': 'Not exported yet',
};

/**
 * How far the business date is from closed. One bar shows the whole date at once: closed in navy,
 * ready in green, blocked in amber, and stores that have not opened a day left empty.
 */
function CloseStrip({ data }: { data: Close | Failed }) {
  if (failed(data)) {
    return (
      <Panel title="Close" href="/store-days" linkLabel="Open Store Days">
        <p className="page-sub">{data.error}</p>
      </Panel>
    );
  }
  const notOpened = Math.max(0, data.expected_stores - data.opened);
  const others = Math.max(0, data.opened - data.closed - data.ready - data.blocked);
  const total = Math.max(data.closed + data.ready + data.blocked + others + notOpened, 1);
  const parts = [
    { key: 'closed', label: 'Closed', n: data.closed, color: 'var(--primary)' },
    { key: 'ready', label: 'Ready to close', n: data.ready, color: 'var(--success)' },
    { key: 'blocked', label: 'Blocked', n: data.blocked, color: 'var(--warning)' },
    { key: 'other', label: 'Not checked', n: others, color: 'var(--fg-tertiary)' },
    { key: 'none', label: 'No store day yet', n: notOpened, color: 'var(--border)' },
  ].filter((p) => p.n > 0);
  const done = data.closed === total;
  const exportRows = EXPORT_ORDER.filter((k) => data.export[k]);
  return (
    <Panel
      title={`Close for ${day(data.business_date)}`}
      href="/store-days"
      linkLabel="Open Store Days"
    >
      <div className="stack" style={{ display: 'flex', flexDirection: 'column' }}>
        <p style={{ margin: 0, fontSize: 'var(--text-base)' }}>
          <b>
            {num(data.closed)} of {num(total)}
          </b>{' '}
          store days closed.
          {done
            ? ' The business date is fully closed.'
            : ` ${num(data.ready)} ready to close, ${num(data.blocked)} blocked${
                notOpened ? `, ${num(notOpened)} with no store day yet` : ''
              }.`}
        </p>
        <div
          role="img"
          aria-label={parts.map((p) => `${p.label}: ${p.n}`).join(', ')}
          style={{
            display: 'flex',
            height: 10,
            borderRadius: 'var(--radius-pill)',
            overflow: 'hidden',
            background: 'var(--bg-tertiary)',
            gap: 2,
          }}
        >
          {parts.map((p) => (
            <div
              key={p.key}
              title={`${p.label}: ${p.n}`}
              style={{ flex: p.n, background: p.color }}
            />
          ))}
        </div>
        <ul
          className="cluster"
          style={{
            listStyle: 'none',
            margin: 0,
            padding: 0,
            gap: 'var(--space-lg)',
            rowGap: 'var(--space-xs)',
            fontSize: 'var(--text-sm)',
            color: 'var(--fg-secondary)',
          }}
        >
          {parts.map((p) => (
            <li
              key={p.key}
              className="cluster"
              style={{ gap: 'var(--space-xs)', alignItems: 'center' }}
            >
              <span
                aria-hidden
                style={{ width: 10, height: 10, borderRadius: 2, background: p.color }}
              />
              {p.label} <b style={{ color: 'var(--fg)' }}>{num(p.n)}</b>
            </li>
          ))}
        </ul>
        {(data.blocked > 0 || exportRows.length > 0) && (
          <div style={grid(260)}>
            {data.blocked > 0 && (
              <div>
                <div style={{ fontSize: 'var(--text-sm)', fontWeight: 'var(--weight-semibold)' }}>
                  What is blocking
                </div>
                {data.blocked_reasons.map((r) => (
                  <Row key={r.reason} label={r.reason} value={plural(r.store_days, 'store day')} />
                ))}
                {data.blocked_examples.length > 0 && (
                  <ul
                    className="summary-list"
                    aria-label="Example store days blocked from closing"
                    style={{ margin: 'var(--space-sm) 0 0', fontSize: 'var(--text-xs)' }}
                  >
                    {data.blocked_examples.slice(0, 3).map((example) => (
                      <li key={example.store_id}>
                        <Link
                          href={`/store-days/${encodeURIComponent(example.store_id)}/${encodeURIComponent(data.business_date)}`}
                        >
                          Review {example.store_id}
                        </Link>
                        <span className="page-sub"> · {example.reasons.join('; ')}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
            {exportRows.length > 0 && (
              <div>
                <div style={{ fontSize: 'var(--text-sm)', fontWeight: 'var(--weight-semibold)' }}>
                  Export of closed days
                </div>
                {exportRows.map((k) => (
                  <Row key={k} label={EXPORT_LABEL[k] ?? k} value={num(data.export[k])} />
                ))}
              </div>
            )}
          </div>
        )}
        {data.unchecked > 0 && (
          <p className="page-sub" style={{ margin: 0, fontSize: 'var(--text-xs)' }}>
            {num(data.unchecked)} more open store days were not checked here. See Store Days for all
            of them.
          </p>
        )}
      </div>
    </Panel>
  );
}

/* ---------- Trends ---------- */

/** Cases raised per business day, on one scale. The busiest day is drawn in full navy. */
function Trend({ days }: { days: { date: string; count: number }[] }) {
  if (!days.length) return <p className="page-sub">No cases raised in this period.</p>;
  const max = Math.max(...days.map((d) => d.count), 1);
  const total = days.reduce((sum, d) => sum + d.count, 0);
  return (
    <div className="stack-sm" style={{ display: 'flex', flexDirection: 'column' }}>
      <p style={{ margin: 0, fontSize: 'var(--text-sm)', color: 'var(--fg-secondary)' }}>
        <b style={{ color: 'var(--fg)' }}>{num(total)}</b> raised, about{' '}
        {(total / days.length).toFixed(1)} a day
      </p>
      <div
        role="img"
        aria-label={`Cases raised per business day from ${day(days[0].date)} to ${day(days[days.length - 1].date)}; ${total} in total`}
        style={{
          display: 'flex',
          alignItems: 'flex-end',
          gap: 3,
          height: 96,
          borderBottom: '1px solid var(--border)',
        }}
      >
        {days.map((d) => (
          <div
            key={d.date}
            title={`${day(d.date)}: ${d.count}`}
            style={{
              flex: 1,
              height: `${Math.max(4, (d.count / max) * 100)}%`,
              background:
                d.count === max
                  ? 'var(--primary)'
                  : 'color-mix(in oklab, var(--primary) 45%, var(--bg-secondary))',
              borderRadius: '2px 2px 0 0',
            }}
          />
        ))}
      </div>
      <div
        className="cluster"
        style={{
          justifyContent: 'space-between',
          fontSize: 'var(--text-xs)',
          color: 'var(--fg-tertiary)',
        }}
      >
        <span>{day(days[0].date)}</span>
        <span>{day(days[days.length - 1].date)}</span>
      </div>
    </div>
  );
}

/** A ranked list with a bar per row, so the leader is obvious at a glance. */
function Ranked({ rows }: { rows: Hotspot[] }) {
  if (!rows.length) return <p className="page-sub">No open cases.</p>;
  const max = Math.max(...rows.map((r) => r.open), 1);
  return (
    <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>
      {rows.map((r, i) => (
        <li
          key={r.name}
          style={{ padding: 'var(--space-sm) 0', borderTop: i ? HAIRLINE : undefined }}
        >
          <div
            className="cluster"
            style={{
              justifyContent: 'space-between',
              alignItems: 'baseline',
              fontSize: 'var(--text-sm)',
            }}
          >
            <span>{r.name}</span>
            <span style={{ fontVariantNumeric: 'tabular-nums' }}>
              {r.past_sla > 0 && (
                <span
                  style={{
                    color: 'var(--error)',
                    fontSize: 'var(--text-xs)',
                    marginRight: 'var(--space-sm)',
                  }}
                >
                  {r.past_sla} past SLA
                </span>
              )}
              <b>{num(r.open)}</b>
            </span>
          </div>
          <div
            aria-hidden
            style={{
              marginTop: 'var(--space-xs)',
              height: 4,
              width: `${Math.max(4, (r.open / max) * 100)}%`,
              background: 'var(--primary)',
              borderRadius: 'var(--radius-pill)',
            }}
          />
        </li>
      ))}
    </ol>
  );
}

function Freshness({ data }: { data: BatchStatus }) {
  if (data.freshness === 'fresh') {
    return (
      <Badge variant="success" size="sm">
        Data as of {data.data_as_of ? day(data.data_as_of) : 'unknown'}
      </Badge>
    );
  }
  const text =
    data.freshness === 'never'
      ? 'No successful batch yet'
      : `Data is ${Math.round(data.hours_since_success ?? 0)}h old`;
  return (
    <Badge variant="warning" size="sm">
      {text}
      {data.last_failed ? ', last run failed' : ''}
    </Badge>
  );
}

const grid = (min: number): React.CSSProperties => ({
  display: 'grid',
  gap: 'var(--space-md)',
  gridTemplateColumns: `repeat(auto-fit, minmax(min(100%, ${min}px), 1fr))`,
  alignItems: 'start',
});

/** Ongoing monitoring: how accurate, how exception-prone and how timely the audit has been lately. */
function KpiStrip({ data }: { data: Kpis | Failed }) {
  if (failed(data)) return null;
  const pct = (v: number | null) => (v === null ? '—' : `${v}%`);
  const cards = [
    {
      title: 'Transaction accuracy',
      value: pct(data.accuracy),
      note: `${formatNumber(data.flagged_transactions)} of ${formatNumber(data.transactions)} transactions flagged`,
    },
    {
      title: 'Exception rate',
      value: data.exception_rate === null ? '—' : `${data.exception_rate} per 1,000`,
      note: 'Exceptions raised per 1,000 transactions',
    },
    {
      title: 'Timeliness',
      value: pct(data.timeliness),
      note: `${data.closed_on_time} of ${data.due_store_days} store days closed within ${data.close_target_days} days`,
    },
  ];
  return (
    <Group title={`Ongoing monitoring, last ${data.window_days} days`}>
      <div style={grid(240)}>
        {cards.map((k) => (
          <Panel key={k.title} title={k.title}>
            <b style={{ fontSize: 'var(--text-xl)', color: 'var(--fg)' }}>{k.value}</b>
            <p className="page-sub">{k.note}</p>
          </Panel>
        ))}
      </div>
    </Group>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section
      aria-label={title}
      style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}
    >
      <h2
        style={{
          margin: 0,
          fontSize: 'var(--text-lg)',
          fontWeight: 'var(--weight-semibold)',
          color: 'var(--fg)',
        }}
      >
        {title}
      </h2>
      {children}
    </section>
  );
}

/* ---------- Page ---------- */

export function CommandCenter() {
  const router = useRouter();
  const canOpen = useCanOpen();
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [eligible, setEligible] = useState<Eligible | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [assigning, setAssigning] = useState<string | null>(null);
  const { me } = useMe();
  const toast = useToast();

  // Since your last visit: the picture this browser kept when you last left the page.
  const userKey = me?.user ?? null;
  const [baseline, setBaseline] = useState<Seen | null>(null);
  const [fromLastVisit, setFromLastVisit] = useState(false);
  const started = useRef(false);
  const latest = useRef<Seen | null>(null);
  const current = useMemo(
    () =>
      data
        ? snapshot(
            {
              counts: ok(data.work)?.counts,
              // Both tables, so a case moving from one to the other is not called new.
              queue: [...(ok(data.work)?.queue ?? []), ...(ok(data.work)?.mine ?? [])],
              failedExports: ok(data.exports)?.failed_batches,
              feeds: ok(data.feeds)?.feeds,
              close: ok(data.close),
            },
            updatedAt ?? new Date()
          )
        : null,
    [data, updatedAt]
  );
  useEffect(() => {
    if (!current || !userKey) return;
    latest.current = current;
    if (started.current) return;
    started.current = true;
    const stored = readSeen(userKey);
    setBaseline(stored ?? current);
    setFromLastVisit(!!stored);
    if (!stored) writeSeen(userKey, current);
  }, [current, userKey]);
  useEffect(() => {
    if (!userKey) return;
    const save = () => {
      if (latest.current) writeSeen(userKey, latest.current);
    };
    const onHide = () => {
      if (document.hidden) save();
    };
    document.addEventListener('visibilitychange', onHide);
    window.addEventListener('pagehide', save);
    return () => {
      save();
      document.removeEventListener('visibilitychange', onHide);
      window.removeEventListener('pagehide', save);
    };
  }, [userKey]);
  const markSeen = () => {
    if (!current || !userKey) return;
    setBaseline(current);
    setFromLastVisit(false);
    writeSeen(userKey, current);
  };
  const changed = baseline && current ? changes(baseline, current) : [];
  const since = baseline ? when(baseline.at) : '';
  const moved = (k: Counter) => {
    const a = baseline?.counts[k];
    const b = current?.counts[k];
    return a === undefined || b === undefined ? undefined : { by: b - a, since };
  };

  // What this person may do from here. The server checks every action again.
  const role = me?.role ?? '';
  const may = {
    assign: ['finance', 'it', 'sales_audit_manager', 'admin'].includes(role) && canOpen('/actions'),
    approve: ['finance', 'admin'].includes(role) && canOpen('/actions'),
    retry: ['it', 'admin'].includes(role) && canOpen('/exports'),
    rerun: ['it', 'admin'].includes(role) && canOpen('/imports'),
  };

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      setData((await api.get<Overview>('/api/command-center')).data);
      setUpdatedAt(new Date());
      setError('');
    } catch {
      setError('The Command Center could not be loaded. Check your connection and retry.');
    } finally {
      setRefreshing(false);
    }
  }, []);
  useLive(load, REFRESH_MS);

  // One-click approvals are a Finance feature; anyone else never asks for them.
  const loadEligible = useCallback(async () => {
    try {
      setEligible((await api.get<Eligible>('/api/agent/eligible')).data);
    } catch {
      setEligible(null);
    }
  }, []);
  useEffect(() => {
    if (may.approve && data) void loadEligible();
  }, [may.approve, data, loadEligible]);

  const reason = (e: unknown) =>
    (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  const failText = (e: unknown, fallback: string) => {
    const d = reason(e);
    return typeof d === 'string' ? d : fallback;
  };

  const assignToMe = async (r: QueueCase) => {
    setAssigning(r.id);
    try {
      const res = await api.post<{ ok: number; skipped: { reason: string }[] }>(
        '/api/action-center/bulk',
        { action: 'assign', ids: [r.id], params: { assignee: 'me' } }
      );
      if (res.data.ok) toast(`${r.case_number} is assigned to you.`, 'success');
      else
        toast(
          `${r.case_number} was not assigned: ${res.data.skipped[0]?.reason ?? 'skipped'}.`,
          'error'
        );
    } catch (e) {
      toast(failText(e, `${r.case_number} could not be assigned.`), 'error');
    } finally {
      setAssigning(null);
    }
  };

  const confirmApprove = (list: Eligible) =>
    setPending({
      title: `Approve ${plural(list.cases.length, 'case')}`,
      confirm: `Approve ${plural(list.cases.length, 'case')}`,
      body: (
        <div className="stack-sm" style={{ display: 'flex', flexDirection: 'column' }}>
          <p style={{ margin: 0 }}>
            The investigator agent prepared these and found complete evidence. Each is a timing
            difference that moves no money. The policy is checked again for every case, and any that
            no longer qualify are skipped.
          </p>
          <ul style={{ margin: 0, paddingLeft: 'var(--space-lg)', fontSize: 'var(--text-sm)' }}>
            {list.cases.map((x) => (
              <li key={x.case_id}>
                {x.case_number}, exception {caseMoney(x.amount, x.currency)}
              </li>
            ))}
          </ul>
        </div>
      ),
      run: async () => {
        const res = await api.post<{
          ok: number;
          skipped_count: number;
          skipped: { reason: string }[];
        }>('/api/action-center/bulk', { action: 'approve', ids: list.cases.map((x) => x.case_id) });
        const { ok: approved, skipped_count } = res.data;
        toast(
          skipped_count
            ? `Approved ${approved}. ${skipped_count} skipped: ${res.data.skipped[0]?.reason ?? 'see the Action Center'}.`
            : `Approved ${plural(approved, 'case')}.`,
          approved ? 'success' : 'error'
        );
        void loadEligible();
      },
    });

  const confirmRetry = (b: FailedBatch) =>
    setPending({
      title: `Retry delivery to ${b.destination}`,
      confirm: 'Retry delivery',
      body: (
        <p style={{ margin: 0 }}>
          This sends the {b.dataset} batch to {b.destination} again. The last attempt failed with:{' '}
          {b.error ?? 'no error recorded'}.
        </p>
      ),
      run: async () => {
        const res = await api.post<{ status?: string }>(`/api/exports/batches/${b.id}/retry`);
        const status = res.data?.status;
        toast(
          status === 'Failed'
            ? `Delivery to ${b.destination} failed again.`
            : `Delivered to ${b.destination}.`,
          status === 'Failed' ? 'error' : 'success'
        );
      },
    });

  const confirmRerun = (businessDate: string) =>
    setPending({
      title: `Rerun the batch for ${day(businessDate)}`,
      confirm: 'Rerun batch',
      body: (
        <p style={{ margin: 0 }}>
          This loads {businessDate} again, re-runs matching and audit, and raises cases for anything
          new. Existing decisions are kept. It can take a few minutes.
        </p>
      ),
      run: async () => {
        const res = await api.post<{ status?: string }>('/api/batch/run', {
          business_date: businessDate,
          rerun: true,
        });
        toast(
          res.data?.status === 'Failed'
            ? `The batch for ${day(businessDate)} failed again. See Imports for the error.`
            : `The batch for ${day(businessDate)} finished.`,
          res.data?.status === 'Failed' ? 'error' : 'success'
        );
      },
    });

  const runPending = async () => {
    if (!pending) return;
    setBusy(true);
    try {
      await pending.run();
      setPending(null);
    } catch (e) {
      toast(
        failText(e, `${pending.title} did not work. Try again or open the related screen.`),
        'error'
      );
    } finally {
      setBusy(false);
    }
  };

  const work = ok(data?.work);
  const c = work?.counts;
  const storeDays = ok(data?.store_days);
  const cash = ok(data?.cash);
  const toActions = canOpen('/actions') ? () => router.push('/actions') : undefined;
  const openCase = canOpen('/cases') ? (r: QueueCase) => router.push(`/cases/${r.id}`) : undefined;

  const columns: Column<QueueCase>[] = [
    {
      key: 'sla',
      header: 'SLA',
      render: (r) =>
        SLA[r.sla_state] ? (
          <span className="cluster" style={{ gap: 'var(--space-xs)', flexWrap: 'nowrap' }}>
            <Badge variant={SLA[r.sla_state].variant} size="sm">
              {SLA[r.sla_state].label}
            </Badge>
            {rowMark(baseline, r.id, r.sla_state) && (
              <span
                title={`Changed since ${since}`}
                style={{
                  fontSize: 'var(--text-xs)',
                  fontWeight: 'var(--weight-semibold)',
                  color: 'var(--primary-text)',
                  whiteSpace: 'nowrap',
                }}
              >
                {rowMark(baseline, r.id, r.sla_state) === 'new' ? 'New' : 'Just breached'}
              </span>
            )}
          </span>
        ) : (
          '–'
        ),
    },
    {
      key: 'case',
      header: 'Case',
      primary: true,
      stopRowClick: true,
      nowrap: true,
      render: (r) => <CaseLink id={r.id}>{r.case_number}</CaseLink>,
    },
    { key: 'type', header: 'Type', render: (r) => r.case_type },
    {
      key: 'store',
      header: 'Store, day',
      nowrap: true,
      render: (r) =>
        canOpen('/store-days') ? (
          <Link
            href={`/store-days/${encodeURIComponent(r.store_id)}/${r.business_date}`}
            onClick={(event) => event.stopPropagation()}
          >
            {r.store_id}, {day(r.business_date)}
          </Link>
        ) : (
          `${r.store_id}, ${day(r.business_date)}`
        ),
    },
    { key: 'status', header: 'Status', render: (r) => <StatusBadge s={r.status} /> },
    {
      key: 'close-impact',
      header: 'Close impact',
      render: (r) =>
        r.close_blocking_errors ? (
          <Badge variant="error" size="sm">
            {r.close_blocking_errors} blocking
          </Badge>
        ) : (
          <Badge variant="secondary" size="sm">
            None
          </Badge>
        ),
    },
    {
      key: 'exposure',
      header: 'Exposure',
      numeric: true,
      render: (r) => (r.amount_needs_review ? 'Under review' : caseMoney(r.exposure, r.currency)),
    },
    {
      key: 'owner',
      header: 'Owner',
      nowrap: true,
      stopRowClick: true,
      render: (r) =>
        r.assigned_to ? (
          <span style={{ color: 'var(--fg-secondary)' }}>{r.assigned_to}</span>
        ) : may.assign ? (
          <Button
            size="sm"
            onClick={() => void assignToMe(r)}
            disabled={assigning === r.id}
            aria-label={`Assign ${r.case_number} to me`}
          >
            {assigning === r.id ? 'Assigning' : 'Assign to me'}
          </Button>
        ) : (
          <span style={{ color: 'var(--fg-tertiary)' }}>Unassigned</span>
        ),
    },
  ];
  const mineColumns = columns.filter((col) => col.key !== 'owner');

  const exposure =
    c &&
    (c.open_exposure !== null && c.currency
      ? caseMoney(c.open_exposure, c.currency)
      : currencyBreakdown(c.amounts_by_currency, 'exposure'));
  const lines = data ? statusLines(data, may.retry ? confirmRetry : undefined) : [];
  const fresh = ok(data?.freshness);
  const rerunDate = fresh?.last_failed ? fresh.last_run?.business_date : undefined;

  return (
    <div className="stack stack-lg command-center">
      {/* The same header as every other page; the situation in one sentence is the subtitle. */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
        <PageHeader
          title="Command Center"
          subtitle={
            <span aria-live="polite">{c ? headline(c) : 'What needs attention right now'}</span>
          }
          actions={
            <>
              {fresh && <Freshness data={fresh} />}
              {updatedAt && (
                <span
                  style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-tertiary)' }}
                  title="Updates every minute and after any change you make"
                >
                  Updated {formatDateTime(updatedAt, { hour: 'numeric', minute: '2-digit' })}
                </span>
              )}
              {rerunDate && may.rerun && (
                <Button onClick={() => confirmRerun(rerunDate)}>Rerun {day(rerunDate)}</Button>
              )}
              <Button onClick={load} disabled={refreshing}>
                {refreshing ? 'Refreshing' : 'Refresh'}
              </Button>
            </>
          }
        />
        {baseline && (changed.length > 0 || fromLastVisit) && (
          <div
            className="cluster"
            aria-live="polite"
            style={{ alignItems: 'baseline', gap: 'var(--space-sm)', fontSize: 'var(--text-sm)' }}
          >
            {changed.length === 0 ? (
              <span style={{ color: 'var(--fg-secondary)' }}>
                Nothing has changed since {since}.
              </span>
            ) : (
              <>
                <span style={{ color: 'var(--fg-secondary)' }}>Since {since}:</span>
                <span>
                  {changed.map((ch, i) => (
                    <span key={ch.key}>
                      {i > 0 && ', '}
                      <span
                        style={{
                          fontWeight: 'var(--weight-medium)',
                          color:
                            ch.tone === 'worse'
                              ? 'var(--error)'
                              : ch.tone === 'better'
                                ? 'var(--success)'
                                : 'var(--fg)',
                        }}
                      >
                        {ch.text}
                      </span>
                    </span>
                  ))}
                  .
                </span>
                <Button variant="ghost" size="sm" onClick={markSeen}>
                  Mark as seen
                </Button>
              </>
            )}
          </div>
        )}
      </div>

      {error && (
        <Alert tone="error" action={<Button onClick={load}>Retry</Button>}>
          {error}
        </Alert>
      )}
      {!data && !error && <Skeleton />}
      {data && failed(data.work) && (
        <Alert tone="error">Case counts could not be loaded. {data.work.error}</Alert>
      )}

      {c && (
        <div className="stack-sm" style={{ display: 'flex', flexDirection: 'column' }}>
          <div className="cc-summary-band" aria-label="Case priorities">
            <Tile
              label="Past SLA"
              value={c.sla_breached}
              tone="error"
              change={moved('sla_breached')}
              onClick={toActions}
            />
            <Tile
              label="Needs decision"
              value={c.needs_decision}
              tone="warning"
              change={moved('needs_decision')}
              note={c.agent_prepared ? `${num(c.agent_prepared)} prepared by agents` : undefined}
              onClick={toActions}
            />
            <Tile
              label="Due within 8 hours"
              value={c.at_risk}
              tone="warning"
              change={moved('at_risk')}
              onClick={toActions}
            />
            <Tile
              label="Unassigned"
              value={c.unassigned}
              tone="quiet"
              change={moved('unassigned')}
              note={c.snoozed ? `${num(c.snoozed)} snoozed` : undefined}
              onClick={toActions}
            />
          </div>
          <dl
            className="cluster"
            style={{
              margin: 0,
              gap: 'var(--space-xl)',
              rowGap: 'var(--space-xs)',
              fontSize: 'var(--text-sm)',
              color: 'var(--fg-secondary)',
            }}
          >
            {[
              ['Open', `${num(c.open_total)} in ${plural(c.stores_affected, 'store')}`],
              ['Exposure', exposure],
              ['Waiting on evidence', num(c.agent_waiting_evidence)],
              ...(c.straight_through_rate !== null
                ? [['Closed without manual work', `${c.straight_through_rate}%`]]
                : []),
            ].map(([k, v]) => (
              <div key={k} style={{ display: 'flex', gap: 'var(--space-xs)' }}>
                <dt>{k}</dt>
                <dd style={{ margin: 0, color: 'var(--fg)', fontWeight: 'var(--weight-semibold)' }}>
                  {v}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {may.approve && eligible && eligible.count > 0 && (
        <Alert
          tone="info"
          action={
            <Button variant="primary" size="sm" onClick={() => confirmApprove(eligible)}>
              Review and approve {num(eligible.cases.length)}
            </Button>
          }
        >
          {plural(eligible.count, 'case is', 'cases are')} ready for one-click approval: prepared by
          the agent, evidence complete, no money moves.
          {eligible.count > eligible.cases.length
            ? ` The first ${eligible.cases.length} are shown; approve the rest in the Action Center.`
            : ''}
        </Alert>
      )}

      {data && (work || lines.length > 0) && (
        <div className="cc-workspace">
          {work && (
            <div className="stack cc-work-queue">
              <Panel title="Work first" href="/actions" linkLabel="Open the Action Center" flush>
                <DataTable
                  rows={work.queue}
                  rowKey={(r) => r.id}
                  columns={columns}
                  onRowClick={openCase}
                  emptyTitle="Nothing waiting"
                  emptyHint="Every open case is on track or snoozed."
                />
              </Panel>
              <Panel title={`Assigned to you (${work.mine.length})`} flush>
                <DataTable
                  rows={work.mine}
                  rowKey={(r) => r.id}
                  columns={mineColumns}
                  onRowClick={openCase}
                  emptyTitle="Nothing assigned to you"
                  emptyHint="Pick up a case from the Action Center to see it here."
                />
              </Panel>
            </div>
          )}
          {(lines.length > 0 || data.activity) && (
            <div className="stack cc-operations">
              <StatusBoard lines={lines} />
              {data.activity && <ActivityFeed data={data.activity} />}
            </div>
          )}
        </div>
      )}

      {data?.close && <CloseStrip data={data.close} />}

      {data?.kpis && <KpiStrip data={data.kpis} />}

      {work && c && (
        <Group title="Where work is piling up">
          <div style={grid(300)}>
            <Panel title="Cases raised, last 14 business days">
              <Trend days={c.by_day} />
            </Panel>
            <Panel title="Most open cases by store">
              <Ranked rows={work.hotspots.stores} />
            </Panel>
            <Panel title="Most open cases by type">
              <Ranked rows={work.hotspots.types} />
            </Panel>
          </div>
        </Group>
      )}

      {data && (data.cash || storeDays) && (
        <Group title="Stores and cash">
          <div style={grid(300)}>
            {storeDays && (
              <Panel
                title={`Late stores for ${day(storeDays.business_date)}`}
                href="/store-days"
                linkLabel="Open Store Days"
              >
                {storeDays.late_stores.length === 0 ? (
                  <p className="page-sub">Every store has sent its data.</p>
                ) : (
                  <>
                    {storeDays.late_stores.map((s) => (
                      <Row key={s.store_id} label={s.store_id} value={s.state} tone="warning" />
                    ))}
                    {storeDays.late_total > storeDays.late_stores.length && (
                      <p className="page-sub" style={{ margin: 'var(--space-sm) 0 0' }}>
                        {num(storeDays.late_total - storeDays.late_stores.length)} more in Store
                        Days
                      </p>
                    )}
                  </>
                )}
              </Panel>
            )}
            {data.cash && (
              <Panel
                title={`Cash, last ${cash?.window_days ?? 7} days`}
                href="/cash-office"
                linkLabel="Open Cash Office"
              >
                {cash ? (
                  <>
                    <Row label="Declarations" value={num(cash.declarations)} />
                    <Row
                      label="Out of balance"
                      value={num(cash.out_of_balance)}
                      tone={cash.out_of_balance ? 'warning' : undefined}
                    />
                    <Row
                      label="Short"
                      value={num(cash.short)}
                      tone={Number(cash.short) > 0 ? 'error' : undefined}
                    />
                    <Row label="Over" value={num(cash.over)} />
                  </>
                ) : (
                  <p className="page-sub">{(data.cash as Failed).error}</p>
                )}
              </Panel>
            )}
            {cash && (
              <Panel title="Vouchers" href="/cash-office" linkLabel="Open Cash Office">
                <Row label="Outstanding" value={voucherText(cash.vouchers_outstanding)} />
                <Row
                  label="Expired, unclaimed"
                  value={voucherText(cash.vouchers_expired)}
                  tone={cash.vouchers_expired.length ? 'warning' : undefined}
                />
              </Panel>
            )}
          </div>
        </Group>
      )}
      {pending && (
        <Modal
          title={pending.title}
          onClose={() => !busy && setPending(null)}
          footer={
            <>
              <Button onClick={() => setPending(null)} disabled={busy}>
                Cancel
              </Button>
              <Button variant="primary" onClick={() => void runPending()} disabled={busy}>
                {busy ? 'Working' : pending.confirm}
              </Button>
            </>
          }
        >
          {pending.body}
        </Modal>
      )}
    </div>
  );
}
