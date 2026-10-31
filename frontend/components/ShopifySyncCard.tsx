'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { useToast } from '@/components/Feedback';
import { Alert, Button, Card, Field } from '@/components/ui';
import type { Source } from '@/lib/sources';

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const RANGES = [
  { value: '24', label: 'Last 24 hours' },
  { value: '168', label: 'Last 7 days' },
  { value: '720', label: 'Last 30 days' },
  { value: 'custom', label: 'Choose dates…' },
];
const MAX_CUSTOM_DAYS = 90;
const apiError = (e: unknown, fallback: string) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;
const when = (iso: string) => new Date(iso).toLocaleString();
const localDay = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

interface Props {
  source: Source;
  canManage: boolean;
  onUpdated: (s: Source) => void;
  onSynced: () => void;
}

/** Pull Shopify orders now, for any range, or on a daily or weekly schedule. */
export function ShopifySyncCard({ source: s, canManage, onUpdated, onSynced }: Props) {
  const toast = useToast();
  const today = localDay(new Date());
  const [range, setRange] = useState('24');
  const [from, setFrom] = useState(today);
  const [to, setTo] = useState(today);
  const [syncing, setSyncing] = useState(false);
  const [frequency, setFrequency] = useState<'off' | 'daily' | 'weekly'>(s.sync_frequency ?? 'off');
  const [weekday, setWeekday] = useState(s.sync_weekday ?? 0);
  const [time, setTime] = useState(s.sync_time ?? '02:00');
  const [saving, setSaving] = useState(false);
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;

  // Follow the saved schedule when it changes (after a save or a reload).
  useEffect(() => {
    setFrequency(s.sync_frequency ?? 'off');
    setWeekday(s.sync_weekday ?? 0);
    setTime(s.sync_time ?? '02:00');
  }, [s.sync_frequency, s.sync_weekday, s.sync_time]);

  const why = !s.configured
    ? 'Shopify is not connected. The store URL and access token are set on the backend (see Configuration, Integrations).'
    : !s.enabled
      ? 'Shopify is switched off above.'
      : '';
  const syncBlocked =
    why ||
    (!s.manual_sync ? 'Manual sync is switched off for Shopify above.' : '') ||
    (!canManage ? 'Only an IT administrator can run a sync.' : '');
  const customDays =
    Math.round((new Date(to).getTime() - new Date(from).getTime()) / 86_400_000) + 1;
  const customProblem =
    range !== 'custom'
      ? ''
      : !from || !to
        ? 'Choose both dates.'
        : customDays < 1
          ? 'The end date is before the start date.'
          : customDays > MAX_CUSTOM_DAYS
            ? `Choose at most ${MAX_CUSTOM_DAYS} days at a time.`
            : '';

  const sync = async () => {
    setSyncing(true);
    try {
      let min: Date;
      let max: Date;
      if (range === 'custom') {
        min = new Date(`${from}T00:00:00`); // local midnight
        max = new Date(`${to}T00:00:00`);
        max.setDate(max.getDate() + 1); // through the end of the last day
        if (max.getTime() > Date.now()) max = new Date();
      } else {
        max = new Date();
        min = new Date(max.getTime() - Number(range) * 3_600_000);
      }
      const { data } = await api.post('/api/ingest/shopify/sync', {
        created_at_min: min.toISOString(),
        created_at_max: max.toISOString(),
      });
      toast(
        `Shopify sync: ${data.received} received, ${data.canonical_created} new transactions, ${data.quarantined} quarantined.`,
        'success'
      );
      onSynced();
    } catch (e) {
      toast(apiError(e, 'Shopify sync failed.'), 'error');
    } finally {
      setSyncing(false);
    }
  };

  const unchanged =
    frequency === (s.sync_frequency ?? 'off') &&
    (frequency === 'off' ||
      (time === s.sync_time && (frequency === 'daily' || weekday === s.sync_weekday)));
  const save = async () => {
    setSaving(true);
    try {
      const body =
        frequency === 'off'
          ? { sync_frequency: 'off' }
          : {
              sync_frequency: frequency,
              sync_time: time,
              sync_timezone: zone,
              ...(frequency === 'weekly' ? { sync_weekday: weekday } : {}),
            };
      const { data } = await api.put(`/api/ingest/sources/${s.source_system}`, body);
      onUpdated(data);
      toast(frequency === 'off' ? 'Schedule turned off.' : 'Schedule saved.', 'success');
    } catch (e) {
      toast(apiError(e, 'Could not save the schedule.'), 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title="Shopify sync">
      {why && <Alert tone="info">{why}</Alert>}

      <h3 className="sd-day">Sync now</h3>
      <p className="hint">
        An ad-hoc pull of orders and refunds for any period, without waiting for the nightly batch
        or the schedule. Orders already loaded are skipped.
      </p>
      {syncBlocked && !why && <Alert tone="info">{syncBlocked}</Alert>}
      <div className="form-row">
        <Field label="Orders to pull">
          <select
            value={range}
            onChange={(e) => setRange(e.target.value)}
            disabled={!!syncBlocked || syncing}
          >
            {RANGES.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </Field>
        {range === 'custom' && (
          <>
            <Field label="From">
              <input
                type="date"
                value={from}
                max={today}
                onChange={(e) => setFrom(e.target.value)}
              />
            </Field>
            <Field label="To">
              <input type="date" value={to} max={today} onChange={(e) => setTo(e.target.value)} />
            </Field>
          </>
        )}
        <Button
          variant="primary"
          disabled={!!syncBlocked || syncing || !!customProblem}
          onClick={sync}
        >
          {syncing ? 'Syncing…' : 'Sync now'}
        </Button>
      </div>
      {customProblem && <p className="hint">{customProblem}</p>}

      <h3 className="sd-day">Run on a schedule</h3>
      <p className="hint">
        Pull recent orders and refunds automatically, daily or weekly at a time you choose. Each run
        covers a window wider than its period, so a missed run or a late refund is still caught.
        {s.in_daily_batch && s.enabled ? ' This is in addition to the nightly batch.' : ''}
      </p>
      <div className="form-row">
        <Field label="Repeat">
          <select
            value={frequency}
            onChange={(e) => setFrequency(e.target.value as 'off' | 'daily' | 'weekly')}
            disabled={!canManage || !!why}
          >
            <option value="off">Off</option>
            <option value="daily">Every day</option>
            <option value="weekly">Every week</option>
          </select>
        </Field>
        {frequency === 'weekly' && (
          <Field label="On">
            <select
              value={weekday}
              onChange={(e) => setWeekday(Number(e.target.value))}
              disabled={!canManage || !!why}
            >
              {WEEKDAYS.map((d, i) => (
                <option key={d} value={i}>
                  {d}
                </option>
              ))}
            </select>
          </Field>
        )}
        {frequency !== 'off' && (
          <Field
            label="At"
            hint={`Time zone: ${s.sync_timezone && unchanged ? s.sync_timezone : zone}`}
          >
            <input
              type="time"
              value={time}
              onChange={(e) => setTime(e.target.value)}
              disabled={!canManage || !!why}
            />
          </Field>
        )}
        <Button
          variant="primary"
          disabled={!canManage || !!why || saving || unchanged || !time}
          onClick={save}
        >
          {saving ? 'Saving…' : 'Save schedule'}
        </Button>
      </div>
      <p className="hint">
        {s.sync_frequency
          ? `${s.schedule}.${s.next_run_at ? ` Next run ${when(s.next_run_at)}.` : ''}${
              s.last_scheduled_run_at && s.last_scheduled_status
                ? ` Last run ${when(s.last_scheduled_run_at)}: ${s.last_scheduled_status}${s.last_scheduled_message ? ` (${s.last_scheduled_message})` : ''}.`
                : ''
            }`
          : 'Not scheduled.'}
      </p>
      {s.last_scheduled_status === 'Failed' && (
        <Alert tone="error">
          The last scheduled sync failed: {s.last_scheduled_message}. It will try again at the next
          scheduled time.
        </Alert>
      )}
    </Card>
  );
}
