'use client';
import { formatNumber } from '@/lib/preferences';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { CopyButton } from '@/components/Feedback';
import { Alert, Button, Card, MetricGrid, MetricTile, PageHeader } from '@/components/ui';
import { apiError } from '@/lib/storeDays';

interface Window {
  supported: boolean;
  reason?: string;
  from_seq: number;
  checked: number;
  intact: boolean;
  broken_events: number;
  first_broken_seq: number | null;
  next_seq: number | null;
  done: boolean;
  head_seq: number | null;
  head_hash: string | null;
  first_seq: number | null;
  verified_at: string;
  note: string;
}
interface Scan {
  checked: number;
  broken: number;
  firstBroken: number | null;
  through: number;
  headSeq: number;
  finished: boolean;
}

const PATH = '/api/diagnostics/integrity';
const FULL_SCAN_WINDOW = 20000;

/**
 * Proves the audit trail has not been edited. The quick check covers the newest events; the full
 * check walks the whole trail a window at a time, so it can be stopped and never holds the
 * database for long.
 */
export function AuditIntegrity() {
  const [latest, setLatest] = useState<Window | null>(null);
  const [scan, setScan] = useState<Scan | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stop = useRef(false);

  const check = useCallback(async () => {
    try {
      setLatest((await api.get<Window>(PATH)).data);
      setError(null);
    } catch (e) {
      setError(apiError(e, 'Could not verify the audit trail.'));
    }
  }, []);
  useEffect(() => {
    check();
  }, [check]);

  const verifyAll = async () => {
    stop.current = false;
    setRunning(true);
    setError(null);
    let from = 0;
    let checked = 0;
    let broken = 0;
    let firstBroken: number | null = null;
    try {
      for (;;) {
        const { data } = await api.get<Window>(PATH, {
          params: { from_seq: from, size: FULL_SCAN_WINDOW },
        });
        checked += data.checked;
        broken += data.broken_events;
        firstBroken = firstBroken ?? data.first_broken_seq;
        const finished = data.done;
        setScan({
          checked,
          broken,
          firstBroken,
          through: data.next_seq ? data.next_seq - 1 : (data.head_seq ?? 0),
          headSeq: data.head_seq ?? 0,
          finished,
        });
        if (finished || stop.current || data.next_seq === null) break;
        from = data.next_seq;
      }
    } catch (e) {
      setError(apiError(e, 'The verification stopped because a window could not be checked.'));
    } finally {
      setRunning(false);
    }
  };

  const unsupported = latest && !latest.supported;
  return (
    <div className="page-container">
      <PageHeader
        title="Audit integrity"
        subtitle="Every audit event carries a hash of the one before it, so an edited or removed event breaks the chain."
        actions={
          <>
            <Button onClick={() => void check()} disabled={running}>
              Re-check latest
            </Button>
            {running ? (
              <Button onClick={() => (stop.current = true)}>Stop</Button>
            ) : (
              <Button variant="primary" onClick={() => void verifyAll()} disabled={!!unsupported}>
                Verify the whole trail
              </Button>
            )}
          </>
        }
      />
      {error && <Alert tone="error">{error}</Alert>}
      {unsupported && <Alert tone="info">{latest.reason}</Alert>}

      {latest?.supported && (
        <Card title="Newest events">
          <Alert tone={latest.intact ? 'success' : 'error'}>
            {latest.intact
              ? `The newest ${formatNumber(latest.checked)} events are intact.`
              : `${latest.broken_events} event(s) do not match their hash, starting at event ${latest.first_broken_seq}. The audit trail may have been altered.`}
          </Alert>
          <MetricGrid>
            <MetricTile label="Events checked" value={formatNumber(latest.checked)} />
            <MetricTile label="Latest event" value={`#${latest.head_seq ?? '—'}`} />
            <MetricTile label="First event" value={`#${latest.first_seq ?? '—'}`} />
          </MetricGrid>
          {latest.head_hash && (
            <p>
              <b>Head hash</b> <code>{latest.head_hash}</code>{' '}
              <CopyButton value={latest.head_hash} label="Copy" />
            </p>
          )}
          <p className="hint">{latest.note}</p>
        </Card>
      )}

      {scan && (
        <Card title="Whole trail">
          <Alert tone={scan.broken > 0 ? 'error' : scan.finished ? 'success' : 'info'}>
            {scan.broken > 0
              ? `${scan.broken} event(s) do not match, starting at event ${scan.firstBroken}.`
              : scan.finished
                ? `All ${formatNumber(scan.checked)} events are intact.`
                : running
                  ? `Checking… ${formatNumber(scan.checked)} events so far, through #${formatNumber(scan.through)} of #${formatNumber(scan.headSeq)}.`
                  : `Stopped after ${formatNumber(scan.checked)} events, through #${formatNumber(scan.through)} of #${formatNumber(scan.headSeq)}.`}
          </Alert>
        </Card>
      )}
    </div>
  );
}
