'use client';

import { useRef, useState } from 'react';
import { api } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { useToast } from '@/components/Feedback';
import { useMe } from '@/components/MeProvider';
import { Alert, Button, DataTable, Field } from '@/components/ui';

type ProfilePackage = {
  format: 'zetsa.source-feed-profiles';
  version: 1;
  profiles: {
    name: string;
    source_system: string;
    schedule: string | null;
    column_mapping: Record<string, string>;
  }[];
};

type Preview = {
  valid: boolean;
  preview_token: string | null;
  errors: { row: number; message: string }[];
  errors_truncated: boolean;
  counts: { create: number; update: number; unchanged: number };
  rows: {
    row: number;
    name: string;
    source_system: string;
    schedule: string | null;
    action: string;
    enabled_after_import: boolean;
  }[];
};

function messageFrom(cause: unknown, fallback: string) {
  const detail = (cause as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && 'errors' in detail) {
    const errors = (detail as { errors?: { row?: number; message?: string }[] }).errors;
    if (errors?.length) return errors.map((item) => `Row ${item.row}: ${item.message}`).join(' · ');
  }
  return fallback;
}

export function SourceFeedProfilePackages({ onApplied }: { onApplied: () => Promise<void> }) {
  const { me } = useMe();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<{
    fileName: string;
    bundle: ProfilePackage;
    result: Preview;
  } | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canApply = me?.role === 'it' || me?.role === 'admin';

  const downloadPackage = async () => {
    setError(null);
    try {
      const { data } = await api.get<ProfilePackage>('/api/ingest/profiles/package');
      downloadBlob(
        `source-feed-profiles-${new Date().toISOString().slice(0, 10)}.json`,
        new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' })
      );
      toast('Source feed profile package downloaded.', 'success');
    } catch (cause) {
      setError(messageFrom(cause, 'Could not download source feed profiles.'));
    }
  };

  const previewPackage = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    setPreview(null);
    try {
      if (file.size > 1_000_000) throw new Error('Profile packages must be smaller than 1 MB.');
      const bundle = JSON.parse(await file.text()) as ProfilePackage;
      const { data } = await api.post<Preview>('/api/ingest/profiles/package/preview', {
        package: bundle,
      });
      setPreview({ fileName: file.name, bundle, result: data });
      setReason('');
    } catch (cause) {
      setError(
        cause instanceof SyntaxError
          ? 'The selected profile package is not valid JSON.'
          : cause instanceof Error && !('response' in cause)
            ? cause.message
            : messageFrom(cause, 'Could not preview this profile package.')
      );
    } finally {
      setBusy(false);
      if (input.current) input.current.value = '';
    }
  };

  const applyPackage = async () => {
    if (!preview?.result.valid || !preview.result.preview_token) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post<{ created: number; updated: number; unchanged: number }>(
        '/api/ingest/profiles/package/apply',
        {
          package: preview.bundle,
          preview_token: preview.result.preview_token,
          reason,
        }
      );
      setPreview(null);
      setReason('');
      await onApplied();
      toast(
        `Profiles applied: ${data.created} created, ${data.updated} updated, ${data.unchanged} unchanged.`,
        'success'
      );
    } catch (cause) {
      setError(messageFrom(cause, 'Could not apply the package. Preview it again and retry.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack stack-sm">
      <p className="hint">
        Packages contain feed names, source types, schedules, and column mappings. They exclude
        connection secrets and source activation switches; new profiles are created disabled.
      </p>
      {error && <Alert tone="error">{error}</Alert>}
      <div className="cluster">
        <Button size="sm" variant="secondary" onClick={() => void downloadPackage()}>
          Download profile package
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
          {busy ? 'Preparing…' : 'Import profile package'}
        </Button>
        {!canApply && (
          <span className="page-sub">IT role is required to preview and apply profiles.</span>
        )}
      </div>
      {preview && (
        <div className="stack stack-sm">
          <p>
            <b>Preview: {preview.fileName}</b> · {preview.result.counts.create} create ·{' '}
            {preview.result.counts.update} update · {preview.result.counts.unchanged} unchanged
          </p>
          {preview.result.errors.length > 0 && (
            <Alert tone="error">
              {preview.result.errors
                .slice(0, 10)
                .map((item) => `Row ${item.row}: ${item.message}`)
                .join(' · ')}
              {preview.result.errors_truncated && ' · Additional errors omitted.'}
            </Alert>
          )}
          <DataTable
            rows={preview.result.rows}
            rowKey={(row) => `${row.row}-${row.name}`}
            columns={[
              { key: 'name', header: 'Name', primary: true, render: (row) => row.name },
              { key: 'source', header: 'Source', render: (row) => row.source_system },
              {
                key: 'schedule',
                header: 'Schedule',
                render: (row) => row.schedule || 'On delivery',
              },
              { key: 'action', header: 'Change', render: (row) => row.action },
              {
                key: 'enabled',
                header: 'After import',
                render: (row) => (row.enabled_after_import ? 'Enabled' : 'Disabled'),
              },
            ]}
          />
          <Field
            label="Reason for import"
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
              disabled={!preview.result.valid || busy || reason.trim().length < 10 || !canApply}
              onClick={() => void applyPackage()}
            >
              {busy ? 'Applying…' : 'Apply package'}
            </Button>
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => setPreview(null)}>
              Cancel
            </Button>
            {!canApply && <span className="page-sub">IT role is required to apply profiles.</span>}
          </div>
        </div>
      )}
    </div>
  );
}
