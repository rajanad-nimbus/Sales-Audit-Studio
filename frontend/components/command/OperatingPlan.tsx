'use client';
import Link from 'next/link';
import { Badge } from '@/components/ui';
import type { BadgeVariant } from '@/components/ui';

export interface Plan {
  as_of: string;
  data: {
    status: 'ok' | 'warning' | 'critical';
    batch: {
      freshness: 'fresh' | 'stale' | 'never';
      last_failed: boolean;
      hours_since_success: number | null;
      data_as_of: string | null;
    };
    silent_feeds: string[];
    feeds: number;
  };
  store_days: {
    open: number;
    examined: number;
    sampled: boolean;
    ready_to_close: number;
    data_incomplete: number;
    needs_attention: number;
    stuck: number;
    stuck_after_days: number;
    late_stores: number;
    expected_stores: number;
    late_date: string;
  };
}

interface Props {
  /** null until the first answer arrives; undefined if it could not be loaded. */
  plan: Plan | null | undefined;
  prepared: number;
  decisions: number;
  awaitingEvidence: number;
  onShowPrepared: () => void;
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export function dataStage(data: Plan['data']): {
  tone: BadgeVariant;
  label: string;
  headline: string;
  detail: string;
} {
  const { batch, silent_feeds: silent } = data;
  const through = batch.data_as_of ? `Data through ${batch.data_as_of}` : 'No successful batch yet';
  const names =
    silent.slice(0, 3).join(', ') + (silent.length > 3 ? ` and ${silent.length - 3} more` : '');
  if (batch.last_failed) {
    return {
      tone: 'error',
      label: 'Batch failed',
      headline: String(silent.length + 1),
      detail: `The last batch failed. ${silent.length ? `Silent: ${names}.` : through}`,
    };
  }
  if (silent.length > 0) {
    return {
      tone: 'warning',
      label: 'Late',
      headline: String(silent.length),
      detail: `${plural(silent.length, 'feed')} silent: ${names}`,
    };
  }
  if (batch.freshness !== 'fresh') {
    return {
      tone: 'warning',
      label: batch.freshness === 'never' ? 'Not run' : 'Stale',
      headline: '1',
      detail:
        batch.freshness === 'never'
          ? 'The daily batch has not run yet'
          : `Last successful batch ${Math.round(batch.hours_since_success ?? 0)} hours ago`,
    };
  }
  return { tone: 'success', label: 'On track', headline: '✓', detail: through };
}

export function storeDayStage(sd: Plan['store_days']): {
  tone: BadgeVariant;
  label: string;
  detail: string;
} {
  const held = sd.needs_attention + sd.data_incomplete;
  const parts = [
    sd.needs_attention > 0 && `${sd.needs_attention} need attention`,
    sd.stuck > 0 && `${sd.stuck} open over ${sd.stuck_after_days} days`,
    sd.late_stores > 0 && `${plural(sd.late_stores, 'store')} late for ${sd.late_date}`,
  ].filter(Boolean);
  if (sd.open === 0) {
    return { tone: 'success', label: 'None open', detail: 'No store day is waiting' };
  }
  const detail = parts.length ? parts.join(' · ') : `${sd.open} open, none held back`;
  const tone: BadgeVariant =
    sd.stuck > 0 ? 'error' : parts.length || held > 0 ? 'warning' : 'success';
  return {
    tone,
    label: tone === 'success' ? 'On track' : sd.stuck > 0 ? 'Stuck' : 'Held back',
    detail: sd.sampled ? `${detail} (newest ${sd.examined} of ${sd.open})` : detail,
  };
}

/** Where today's work stands: data in, store days balanced, then the cases Nimbus prepared. */
export function OperatingPlan({
  plan,
  prepared,
  decisions,
  awaitingEvidence,
  onShowPrepared,
}: Props) {
  const data = plan ? dataStage(plan.data) : null;
  const days = plan ? storeDayStage(plan.store_days) : null;
  const unavailable = plan === undefined;
  const checking = plan === null;

  return (
    <section className="cc-card" style={{ marginBottom: '1rem' }}>
      <div className="cc-head">
        <div>
          <h2>Today’s operating plan</h2>
          <p className="page-sub">
            Start with data readiness, then balance Store Days, then review only the cases Nimbus
            has prepared for you.
          </p>
        </div>
      </div>
      <div className="cc-pipeline" aria-label="Daily operating flow">
        <Link className="cc-stage has" href="/imports">
          <b>{data ? data.headline : '…'}</b>
          <small>
            Confirm data arrived{' '}
            {data && (
              <Badge variant={data.tone} size="sm">
                {data.label}
              </Badge>
            )}
          </small>
          <small className="pl-sub">
            {data ? data.detail : unavailable ? 'Status unavailable' : checking ? 'Checking…' : ''}
          </small>
        </Link>
        <Link className="cc-stage has" href="/store-days">
          <b>{plan ? plan.store_days.ready_to_close : '…'}</b>
          <small>
            Balance Store Days{' '}
            {days && (
              <Badge variant={days.tone} size="sm">
                {days.label}
              </Badge>
            )}
          </small>
          <small className="pl-sub">
            {days
              ? `ready to close · ${days.detail}`
              : unavailable
                ? 'Status unavailable'
                : checking
                  ? 'Checking…'
                  : ''}
          </small>
        </Link>
        <button className="cc-stage has" onClick={onShowPrepared}>
          <b>{prepared}</b>
          <small>Agent prepared</small>
          <small className="pl-sub">Ready for your review</small>
        </button>
        <Link className="cc-stage has" href="/actions">
          <b>{decisions}</b>
          <small>Your decisions</small>
          <small className="pl-sub">Approve, reject, or escalate</small>
        </Link>
      </div>
      {awaitingEvidence > 0 && (
        <p className="hint" style={{ marginTop: '.7rem' }}>
          Nimbus is waiting for evidence on {awaitingEvidence} case
          {awaitingEvidence === 1 ? '' : 's'}; open <Link href="/actions">Action Center</Link> to
          assign or request it.
        </p>
      )}
      <p className="hint" style={{ marginTop: '.45rem' }}>
        Nimbus can ingest, reconcile, collect linked evidence, and propose a disposition. People
        retain control of corrections, approvals, closures, and financial decisions.
      </p>
    </section>
  );
}
