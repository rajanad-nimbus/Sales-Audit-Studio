'use client';

import { useRef, useState } from 'react';
import { api } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { useToast } from '@/components/Feedback';
import { useMe } from '@/components/MeProvider';
import { Alert, Button, DataTable, Field } from '@/components/ui';

interface Options {
  export_unit_of_work: 'Transaction' | 'Store Day';
  store_days_worked_in_order: boolean;
  max_backdated_transaction_days: number | null;
  duplicate_missing_transaction_checks: boolean;
  automatic_audit_after_import: boolean;
  automatic_store_day_close: boolean;
  card_security_level: 'Restricted Access' | 'Block';
  card_number_mask: string;
}
interface Package {
  format: 'zetsa.system-options';
  version: 1;
  options: Options;
}
interface Change {
  key: string;
  label: string;
  current: string | number | boolean | null;
  proposed: string | number | boolean | null;
}
interface Preview {
  valid: boolean;
  preview_token: string;
  changed: boolean;
  current: Options;
  proposed: Options;
  changes: Change[];
}

function errorMessage(cause: unknown, fallback: string) {
  const detail = (cause as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
}

function display(value: Change['current']) {
  if (value === null) return 'Unlimited';
  if (typeof value === 'boolean') return value ? 'Enabled' : 'Disabled';
  return String(value);
}

export function SystemOptionsPackages({ onApplied }: { onApplied: () => Promise<void> }) {
  const { me } = useMe();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<{ fileName: string; bundle: Package } | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canPromote = me?.role === 'it' || me?.role === 'admin';

  const downloadPackage = async () => {
    setError(null);
    try {
      const { data } = await api.get<Package>('/api/system-options/package');
      downloadBlob(
        `system-options-${new Date().toISOString().slice(0, 10)}.json`,
        new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' })
      );
      toast('System options package downloaded.', 'success');
    } catch (cause) {
      setError(errorMessage(cause, 'Could not download the system options package.'));
    }
  };

  const previewPackage = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    setSelected(null);
    setPreview(null);
    try {
      if (file.size > 100_000)
        throw new Error('System options packages must be smaller than 100 KB.');
      const bundle = JSON.parse(await file.text()) as Package;
      const { data } = await api.post<Preview>('/api/system-options/package/preview', {
        package: bundle,
      });
      setSelected({ fileName: file.name, bundle });
      setPreview(data);
      setReason('');
    } catch (cause) {
      setError(
        cause instanceof SyntaxError
          ? 'The selected system options package is not valid JSON.'
          : cause instanceof Error && !('response' in cause)
            ? cause.message
            : errorMessage(cause, 'Could not preview the system options package.')
      );
    } finally {
      setBusy(false);
      if (input.current) input.current.value = '';
    }
  };

  const applyPackage = async () => {
    if (
      !selected ||
      !preview?.valid ||
      !preview.changed ||
      !canPromote ||
      reason.trim().length < 10
    )
      return;
    setBusy(true);
    setError(null);
    try {
      await api.post('/api/system-options/package/apply', {
        package: selected.bundle,
        preview_token: preview.preview_token,
        reason,
      });
      setSelected(null);
      setPreview(null);
      setReason('');
      await onApplied();
      toast('System options package applied.', 'success');
    } catch (cause) {
      setError(errorMessage(cause, 'Could not apply the package. Preview it again and retry.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack stack-sm">
      <p className="hint">
        Promote operational defaults, import timing, and payment-card handling as one reviewed
        package. Applying changes affects future imports and processing; it does not rewrite prior
        audit findings or stored records.
      </p>
      {error && <Alert tone="error">{error}</Alert>}
      <div className="cluster">
        <Button
          size="sm"
          variant="secondary"
          disabled={!canPromote}
          onClick={() => void downloadPackage()}
        >
          Download system options package
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
          disabled={busy || !canPromote}
          onClick={() => input.current?.click()}
        >
          {busy ? 'Preparing…' : 'Preview package import'}
        </Button>
        {!canPromote && (
          <span className="page-sub">IT or Admin role is required to promote options.</span>
        )}
      </div>
      {selected && preview && (
        <div className="stack stack-sm">
          <p>
            <b>Preview: {selected.fileName}</b> · {preview.changes.length} setting
            {preview.changes.length === 1 ? '' : 's'} changed
          </p>
          <DataTable
            rows={preview.changes}
            rowKey={(row) => row.key}
            emptyTitle="System options already match this package"
            columns={[
              { key: 'setting', header: 'Setting', primary: true, render: (row) => row.label },
              { key: 'current', header: 'Current', render: (row) => display(row.current) },
              { key: 'proposed', header: 'Proposed', render: (row) => display(row.proposed) },
            ]}
          />
          <Field
            label="Reason for promotion"
            hint="At least 10 characters; recorded with the before and after values in the audit trail."
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
              disabled={!preview.changed || busy || !canPromote || reason.trim().length < 10}
              onClick={() => void applyPackage()}
            >
              {busy ? 'Applying…' : 'Apply system options'}
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
