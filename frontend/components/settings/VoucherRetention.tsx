'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { api, money } from '@/lib/api';
import { apiError } from '@/lib/storeDays';
import { formatBusinessDate, formatNumber } from '@/lib/preferences';
import { useMe } from '@/components/MeProvider';
import { Alert, Button, Card, DataTable, Field, Modal } from '@/components/ui';

interface Preview {
  tender_type: string;
  purge_days: number;
  eligible_records: number;
  batch_size: number;
  more_eligible: boolean;
  preview_token: string;
  vouchers: {
    voucher_number: string;
    store_id: string;
    issued_on: string;
    status: string;
    finalized_on: string;
    amount: string;
    currency: string;
  }[];
}
interface Result {
  run_id: string;
  archived_records: number;
  purged_records: number;
  archive_hash: string | null;
  more_eligible: boolean;
}

export function VoucherRetention({ reloadKey = 0 }: { reloadKey?: number }) {
  const { me } = useMe();
  const canRun = ['it', 'admin'].includes(me?.role ?? '');
  const [options, setOptions] = useState<
    { tender_type: string; purge_days: number | null }[] | null
  >(null);
  const [tender, setTender] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const close = useCallback(() => {
    if (!busy) setConfirming(false);
  }, [busy]);
  useEffect(() => {
    let active = true;
    api
      .get<{ tender_type: string; purge_days: number | null }[]>('/api/vouchers/options')
      .then(({ data }) => {
        if (!active) return;
        setOptions(data.filter((option) => option.purge_days != null));
        setPreview(null);
        setConfirming(false);
      })
      .catch((e) => {
        if (active) setError(apiError(e, 'Could not load voucher purge windows.'));
      });
    return () => {
      active = false;
    };
  }, [reloadKey]);
  const prepare = async () => {
    if (!tender || busy) return;
    setBusy(true);
    setPreview(null);
    setResult(null);
    setError(null);
    setConfirming(false);
    try {
      const { data } = await api.get<Preview>('/api/vouchers/retention/preview', {
        params: { tender_type: tender },
      });
      setPreview(data);
    } catch (e) {
      setError(apiError(e, 'Could not preview voucher retention.'));
    } finally {
      setBusy(false);
    }
  };
  const execute = async (purge: boolean) => {
    if (!preview || busy || (purge && !acknowledged)) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post<Result>('/api/vouchers/retention/run', {
        tender_type: preview.tender_type,
        preview_token: preview.preview_token,
        confirm_purge: purge,
      });
      setResult(data);
      setPreview(null);
      setConfirming(false);
    } catch (e) {
      setError(apiError(e, 'Voucher retention failed. Preview again before retrying.'));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Voucher archive and purge">
      <p className="hint">
        Preview settled vouchers that have reached their tender's purge window. Every purge first
        creates a durable archive. Retired numbers remain reserved, with their final status visible
        in Cash Office.
      </p>
      {error && !confirming && <Alert tone="error">{error}</Alert>}
      <div className="form-row">
        <Field label="Tender to retain">
          <select
            value={tender}
            disabled={busy}
            onChange={(event) => {
              setTender(event.target.value);
              setPreview(null);
              setResult(null);
              setError(null);
            }}
          >
            <option value="">Choose tender…</option>
            {(options ?? []).map((option) => (
              <option key={option.tender_type} value={option.tender_type}>
                {option.tender_type} · {option.purge_days} days
              </option>
            ))}
          </select>
        </Field>
        <Button
          disabled={!tender || busy || !options?.some((option) => option.tender_type === tender)}
          onClick={() => void prepare()}
        >
          Preview voucher batch
        </Button>
        {['it', 'admin', 'auditor'].includes(me?.role ?? '') && (
          <Link href="/settings/retention">Retention run history</Link>
        )}
      </div>
      {options?.length === 0 && (
        <p className="hint">Configure a purge window in Voucher timing to enable a batch.</p>
      )}
      {preview && (
        <>
          <p>
            {formatNumber(preview.eligible_records)} settled voucher(s) are eligible. This batch
            contains {formatNumber(preview.batch_size)}.
          </p>
          {preview.more_eligible && (
            <p className="hint">Additional eligible vouchers remain for a later batch.</p>
          )}
          <DataTable
            rows={preview.vouchers}
            rowKey={(row) => row.voucher_number}
            emptyTitle="No settled vouchers eligible"
            columns={[
              {
                key: 'number',
                header: 'Voucher',
                primary: true,
                render: (row) => row.voucher_number,
              },
              { key: 'store', header: 'Store', render: (row) => row.store_id },
              { key: 'status', header: 'Final status', render: (row) => row.status },
              {
                key: 'date',
                header: 'Finalized',
                render: (row) => formatBusinessDate(row.finalized_on),
              },
              {
                key: 'amount',
                header: 'Amount',
                numeric: true,
                render: (row) => money(row.amount, row.currency),
              },
            ]}
          />
          {canRun && (
            <div className="form-row">
              <Button
                disabled={busy || preview.batch_size === 0}
                onClick={() => void execute(false)}
              >
                Archive batch
              </Button>
              <Button
                variant="danger"
                disabled={busy || preview.batch_size === 0}
                onClick={() => {
                  setAcknowledged(false);
                  setConfirming(true);
                }}
              >
                Archive and purge…
              </Button>
            </div>
          )}
        </>
      )}
      {result && (
        <Alert tone="success">
          Archived {result.archived_records} voucher(s); purged {result.purged_records}. Run{' '}
          {result.run_id}.
          {result.archive_hash && (
            <p>
              Archive SHA-256: <code>{result.archive_hash}</code>
            </p>
          )}
          {result.more_eligible && <p>Preview the next batch to continue.</p>}
        </Alert>
      )}
      {confirming && preview && (
        <Modal
          title="Archive and purge vouchers"
          onClose={close}
          footer={
            <>
              <Button disabled={busy} onClick={close}>
                Cancel
              </Button>
              <Button
                variant="danger"
                disabled={busy || !acknowledged}
                onClick={() => void execute(true)}
              >
                {busy ? 'Archiving and purging…' : 'Confirm archive and purge'}
              </Button>
            </>
          }
        >
          <p>
            Archive and remove the {preview.batch_size} reviewed {preview.tender_type} voucher(s)
            from the live ledger. Their numbers and final statuses will remain in the retired
            register.
          </p>
          {error && (
            <Alert tone="error">
              {error}
              <Button disabled={busy} onClick={() => void prepare()}>
                Preview again
              </Button>
            </Alert>
          )}
          <Field label="Remove these vouchers after their archive is written">
            <input
              type="checkbox"
              checked={acknowledged}
              disabled={busy}
              onChange={(event) => setAcknowledged(event.target.checked)}
            />
          </Field>
        </Modal>
      )}
    </Card>
  );
}
