'use client';

import { useRef, useState } from 'react';
import { api } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { useToast } from '@/components/Feedback';
import { useMe } from '@/components/MeProvider';
import { Alert, Button, Field } from '@/components/ui';

type TenderPackage = {
  format: 'zetsa.tender-types';
  version: 1;
  tenders: Record<string, unknown>[];
};

type Preview = {
  valid: boolean;
  preview_token: string | null;
  changed: boolean;
  errors: { row: number; error: string }[];
  rows: { code: string; name: string; action: 'Create' | 'Update' | 'Unchanged' }[];
};

function errorMessage(cause: unknown, fallback: string) {
  const detail = (cause as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
}

export function TenderTypePackages({ onApplied }: { onApplied: () => Promise<void> }) {
  const { me } = useMe();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<{ fileName: string; bundle: TenderPackage } | null>(
    null
  );
  const [preview, setPreview] = useState<Preview | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canApply = me?.role === 'finance' || me?.role === 'it';

  const downloadPackage = async () => {
    setError(null);
    try {
      const { data } = await api.get<TenderPackage>('/api/tender-types/package');
      downloadBlob(
        `tender-types-${new Date().toISOString().slice(0, 10)}.json`,
        new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' })
      );
      toast('Tender type package downloaded.', 'success');
    } catch (cause) {
      setError(errorMessage(cause, 'Could not download the tender type package.'));
    }
  };

  const previewPackage = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    setSelected(null);
    setPreview(null);
    try {
      if (file.size > 500_000) throw new Error('Tender packages must be smaller than 500 KB.');
      const bundle = JSON.parse(await file.text()) as TenderPackage;
      const { data } = await api.post<Preview>('/api/tender-types/package/preview', {
        package: bundle,
      });
      setSelected({ fileName: file.name, bundle });
      setPreview(data);
      setReason('');
    } catch (cause) {
      setError(
        cause instanceof SyntaxError
          ? 'The selected file is not valid JSON.'
          : cause instanceof Error && !('response' in cause)
            ? cause.message
            : errorMessage(cause, 'Could not preview the tender type package.')
      );
    } finally {
      setBusy(false);
      if (input.current) input.current.value = '';
    }
  };

  const applyPackage = async () => {
    if (!selected || !preview?.valid || !preview.preview_token || !canApply) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post<{ changed: boolean }>('/api/tender-types/package/apply', {
        package: selected.bundle,
        preview_token: preview.preview_token,
        reason,
      });
      setSelected(null);
      setPreview(null);
      setReason('');
      await onApplied();
      toast(
        data.changed ? 'Tender type package applied.' : 'Tender types already match package.',
        'success'
      );
    } catch (cause) {
      setError(errorMessage(cause, 'Could not apply the package. Preview it again and retry.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack stack-sm mt-md">
      <p className="hint">
        Export tender types as a versioned JSON package. Importing previews which tenders would be
        created or updated; tenders the package does not name are left as they are.
      </p>
      {error && <Alert tone="error">{error}</Alert>}
      <div className="cluster">
        <Button size="sm" variant="secondary" onClick={() => void downloadPackage()}>
          Download tender package
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
          disabled={busy}
          onClick={() => input.current?.click()}
        >
          {busy ? 'Preparing…' : 'Preview package import'}
        </Button>
      </div>
      {selected && preview && (
        <div className="stack stack-sm">
          <p>
            <b>Preview: {selected.fileName}</b> ·{' '}
            {preview.valid
              ? preview.changed
                ? 'Changes detected'
                : 'No changes'
              : `${preview.errors.length} problem(s) found`}
          </p>
          {preview.errors.length > 0 && (
            <ul>
              {preview.errors.map((item) => (
                <li key={item.row}>
                  Row {item.row}: {item.error}
                </li>
              ))}
            </ul>
          )}
          {preview.valid && (
            <ul>
              {preview.rows.map((item) => (
                <li key={item.code}>
                  {item.action}: {item.code} — {item.name}
                </li>
              ))}
            </ul>
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
              disabled={
                !preview.valid || !preview.changed || busy || !canApply || reason.trim().length < 10
              }
              onClick={() => void applyPackage()}
            >
              {busy ? 'Applying…' : 'Apply tender package'}
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
            {!canApply && (
              <span className="page-sub">
                Finance or IT role is required to apply tender types.
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
