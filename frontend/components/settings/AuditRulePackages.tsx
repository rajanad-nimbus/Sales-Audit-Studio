'use client';

import { useRef, useState } from 'react';
import { api } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { useToast } from '@/components/Feedback';
import { useMe } from '@/components/MeProvider';
import { Alert, Button } from '@/components/ui';

type RulePackage = { format: 'zetsa.audit-rules'; version: 1; rules: Record<string, unknown>[] };

type Preview = {
  valid: boolean;
  preview_token: string | null;
  errors: { row: number; error: string }[];
  rows: { row: number; name: string; total_name: string; action: string }[];
};

function errorMessage(cause: unknown, fallback: string) {
  const detail = (cause as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
}

export function AuditRulePackages({ onApplied }: { onApplied: () => Promise<void> }) {
  const { me } = useMe();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<{ fileName: string; bundle: RulePackage } | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canApply = me?.role === 'finance' || me?.role === 'admin';

  const downloadPackage = async () => {
    setError(null);
    try {
      const { data } = await api.get<RulePackage>('/api/audit-rules/package');
      downloadBlob(
        `audit-rules-${new Date().toISOString().slice(0, 10)}.json`,
        new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' })
      );
      toast('Audit rule package downloaded.', 'success');
    } catch (cause) {
      setError(errorMessage(cause, 'Could not download the audit rule package.'));
    }
  };

  const previewPackage = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    setSelected(null);
    setPreview(null);
    try {
      if (file.size > 2_000_000) throw new Error('Rule packages must be smaller than 2 MB.');
      const bundle = JSON.parse(await file.text()) as RulePackage;
      const { data } = await api.post<Preview>('/api/audit-rules/package/preview', {
        package: bundle,
      });
      setSelected({ fileName: file.name, bundle });
      setPreview(data);
    } catch (cause) {
      setError(
        cause instanceof SyntaxError
          ? 'The selected file is not valid JSON.'
          : cause instanceof Error && !('response' in cause)
            ? cause.message
            : errorMessage(cause, 'Could not preview this rule package.')
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
      const { data } = await api.post<{ created: number }>('/api/audit-rules/package/apply', {
        package: selected.bundle,
        preview_token: preview.preview_token,
      });
      setSelected(null);
      setPreview(null);
      await onApplied();
      toast(
        `Imported ${data.created} rule worksheet(s). Submit them for approval to activate.`,
        'success'
      );
    } catch (cause) {
      setError(errorMessage(cause, 'Could not import this package. Preview it again and retry.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack stack-sm mt-md">
      <p className="hint">
        Export rule definitions as a versioned JSON package. Imports are checked like the rule form
        and become disabled worksheets; they still need submission and a second person&apos;s
        approval before they run.
      </p>
      {error && <Alert tone="error">{error}</Alert>}
      <div className="cluster">
        <Button size="sm" variant="secondary" onClick={() => void downloadPackage()}>
          Download rule package
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
              ? `${preview.rows.length} worksheet(s) would be created`
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
                <li key={item.row}>
                  {item.name} on {item.total_name}
                </li>
              ))}
            </ul>
          )}
          <div className="cluster">
            <Button
              size="sm"
              variant="primary"
              disabled={!preview.valid || busy || !canApply}
              onClick={() => void applyPackage()}
            >
              {busy ? 'Importing…' : 'Import as worksheets'}
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
              <span className="page-sub">Finance role is required to import rules.</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
