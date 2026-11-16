'use client';

import { useRef, useState } from 'react';
import { api } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { useToast } from '@/components/Feedback';
import { useMe } from '@/components/MeProvider';
import { Alert, Button, DataTable, Field } from '@/components/ui';

interface Option {
  tender_type: string;
  escheat_days: number;
  purge_days: number | null;
}
interface Routing {
  enabled: boolean;
  hq_location: { country: string; state: string | null } | null;
  income_partner: string | null;
  rules: { country: string; state: string | null; locations: string[] }[];
}
interface VoucherPolicyPackage {
  format: 'zetsa.voucher-policy';
  version: 1;
  options: Option[];
  routing: Routing;
}
interface Preview {
  valid: boolean;
  preview_token: string;
  changed: boolean;
  routing_changed: boolean;
  counts: { create: number; update: number; unchanged: number; remove: number };
  options: {
    tender_type: string;
    action: 'create' | 'update' | 'unchanged' | 'remove';
    current: Option | null;
    proposed: Option | null;
  }[];
  current: { options: Option[]; routing: Routing };
  proposed: { options: Option[]; routing: Routing };
}

function errorMessage(cause: unknown, fallback: string) {
  const detail = (cause as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
}

export function VoucherPolicyPackages({ onApplied }: { onApplied: () => Promise<void> }) {
  const { me } = useMe();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<{
    fileName: string;
    bundle: VoucherPolicyPackage;
  } | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canApply = me?.role === 'it' || me?.role === 'admin';

  const downloadPackage = async () => {
    setError(null);
    try {
      const { data } = await api.get<VoucherPolicyPackage>('/api/vouchers/policy-package');
      downloadBlob(
        `voucher-policy-${new Date().toISOString().slice(0, 10)}.json`,
        new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' })
      );
      toast('Voucher policy package downloaded.', 'success');
    } catch (cause) {
      setError(errorMessage(cause, 'Could not download the voucher policy package.'));
    }
  };

  const previewPackage = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    setSelected(null);
    setPreview(null);
    try {
      if (file.size > 500_000) {
        throw new Error('Voucher policy packages must be smaller than 500 KB.');
      }
      const bundle = JSON.parse(await file.text()) as VoucherPolicyPackage;
      const { data } = await api.post<Preview>('/api/vouchers/policy-package/preview', {
        package: bundle,
      });
      setSelected({ fileName: file.name, bundle });
      setPreview(data);
      setReason('');
    } catch (cause) {
      setError(
        cause instanceof SyntaxError
          ? 'The selected voucher policy package is not valid JSON.'
          : cause instanceof Error && !('response' in cause)
            ? cause.message
            : errorMessage(cause, 'Could not preview this voucher policy package.')
      );
    } finally {
      setBusy(false);
      if (input.current) input.current.value = '';
    }
  };

  const applyPackage = async () => {
    if (!selected || !preview?.valid || !canApply || reason.trim().length < 10) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post<{
        changed: boolean;
        counts: Preview['counts'];
      }>('/api/vouchers/policy-package/apply', {
        package: selected.bundle,
        preview_token: preview.preview_token,
        reason,
      });
      setSelected(null);
      setPreview(null);
      setReason('');
      await onApplied();
      toast(
        data.changed
          ? `Voucher policy applied: ${data.counts.create} created, ${data.counts.update} updated, ${data.counts.remove} removed.`
          : 'Voucher policy already matches the package.',
        'success'
      );
    } catch (cause) {
      setError(errorMessage(cause, 'Could not apply the package. Preview it again and retry.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack stack-sm">
      <p className="hint">
        Promote voucher aging, retention, and jurisdiction rules as one reviewed package. Live
        voucher balances and voucher-specific location evidence are excluded.
      </p>
      {error && <Alert tone="error">{error}</Alert>}
      <div className="cluster">
        <Button
          size="sm"
          variant="secondary"
          disabled={!canApply}
          onClick={() => void downloadPackage()}
        >
          Download voucher policy package
        </Button>
        <input
          ref={input}
          type="file"
          accept=".json,application/json"
          hidden
          onChange={(event) => void previewPackage(event.target.files?.[0])}
        />
        <Button
          size="sm"
          variant="secondary"
          disabled={busy || !canApply}
          onClick={() => input.current?.click()}
        >
          {busy ? 'Preparing…' : 'Preview package import'}
        </Button>
        {!canApply && (
          <span className="page-sub">IT or Admin role is required to promote policy.</span>
        )}
      </div>
      {selected && preview && (
        <div className="stack stack-sm">
          <p>
            <b>Preview: {selected.fileName}</b> · {preview.counts.create} create ·{' '}
            {preview.counts.update} update · {preview.counts.remove} remove ·{' '}
            {preview.counts.unchanged} unchanged · routing{' '}
            {preview.routing_changed ? 'changes detected' : 'unchanged'}
          </p>
          <DataTable
            rows={preview.options}
            rowKey={(row) => row.tender_type}
            emptyTitle="No voucher aging rules in the package"
            columns={[
              {
                key: 'tender',
                header: 'Tender type',
                primary: true,
                render: (row) => row.tender_type,
              },
              { key: 'action', header: 'Action', render: (row) => row.action },
              {
                key: 'escheat',
                header: 'Escheat days',
                numeric: true,
                render: (row) =>
                  `${row.current?.escheat_days ?? '—'} → ${row.proposed?.escheat_days ?? '—'}`,
              },
              {
                key: 'purge',
                header: 'Purge days',
                numeric: true,
                render: (row) =>
                  `${row.current?.purge_days ?? 'Never'} → ${row.proposed?.purge_days ?? 'Never'}`,
              },
            ]}
          />
          {preview.routing_changed && (
            <details>
              <summary>Review jurisdiction routing changes</summary>
              <div className="form-row">
                <Field label="Current routing">
                  <pre className="ex-pre">{JSON.stringify(preview.current.routing, null, 2)}</pre>
                </Field>
                <Field label="Proposed routing">
                  <pre className="ex-pre">{JSON.stringify(preview.proposed.routing, null, 2)}</pre>
                </Field>
              </div>
            </details>
          )}
          <Field
            label="Reason for promotion"
            hint="At least 10 characters; recorded in the audit trail."
          >
            <textarea
              value={reason}
              maxLength={2000}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
          <div className="cluster">
            <Button
              size="sm"
              variant="primary"
              disabled={!preview.changed || busy || !canApply || reason.trim().length < 10}
              onClick={() => void applyPackage()}
            >
              {busy ? 'Applying…' : 'Apply voucher policy'}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={() => {
                setSelected(null);
                setPreview(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
