'use client';

import { useRef, useState } from 'react';
import { api } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { useToast } from '@/components/Feedback';
import { useMe } from '@/components/MeProvider';
import { Alert, Button, Field } from '@/components/ui';

type Rule = {
  total_name: string | null;
  location_trait: string | null;
  within_amount: string;
  case_amount: string;
};

type TolerancePackage = {
  format: 'zetsa.variance-tolerances';
  version: 1;
  tolerances: Rule[];
};

type Preview = {
  valid: boolean;
  preview_token: string;
  changed: boolean;
  created: Rule[];
  updated: (Rule & { current: Pick<Rule, 'within_amount' | 'case_amount'> })[];
  retired: Rule[];
  unchanged: number;
};

const scopeLabel = (rule: Rule) =>
  `${rule.total_name ?? 'Every total'} · ${rule.location_trait ?? 'every store'}`;

function errorMessage(cause: unknown, fallback: string) {
  const detail = (cause as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
}

export function VarianceTolerancePackages({ onApplied }: { onApplied: () => Promise<void> }) {
  const { me } = useMe();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<{ fileName: string; bundle: TolerancePackage } | null>(
    null
  );
  const [preview, setPreview] = useState<Preview | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canApply = me?.role === 'finance';

  const downloadPackage = async () => {
    setError(null);
    try {
      const { data } = await api.get<TolerancePackage>('/api/variance-tolerances/package');
      downloadBlob(
        `variance-tolerances-${new Date().toISOString().slice(0, 10)}.json`,
        new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' })
      );
      toast('Variance tolerance package downloaded.', 'success');
    } catch (cause) {
      setError(errorMessage(cause, 'Could not download the tolerance package.'));
    }
  };

  const previewPackage = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    setSelected(null);
    setPreview(null);
    try {
      if (file.size > 500_000) throw new Error('Tolerance packages must be smaller than 500 KB.');
      const bundle = JSON.parse(await file.text()) as TolerancePackage;
      const { data } = await api.post<Preview>('/api/variance-tolerances/package/preview', {
        package: bundle,
      });
      setSelected({ fileName: file.name, bundle });
      setPreview(data);
      setReason('');
    } catch (cause) {
      setError(
        cause instanceof SyntaxError
          ? 'The selected tolerance package is not valid JSON.'
          : cause instanceof Error && !('response' in cause)
            ? cause.message
            : errorMessage(cause, 'Could not preview the tolerance package.')
      );
    } finally {
      setBusy(false);
      if (input.current) input.current.value = '';
    }
  };

  const applyPackage = async () => {
    if (!selected || !preview?.valid || !canApply) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post<{ changed: boolean }>(
        '/api/variance-tolerances/package/apply',
        { package: selected.bundle, preview_token: preview.preview_token, reason }
      );
      setSelected(null);
      setPreview(null);
      setReason('');
      await onApplied();
      toast(
        data.changed ? 'Variance tolerance package applied.' : 'Tolerances already match package.',
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
        Export the active tolerances as a versioned JSON package. Importing previews what would be
        added, changed and retired, and replaces the active set only when applied.
      </p>
      {error && <Alert tone="error">{error}</Alert>}
      <div className="cluster">
        <Button size="sm" variant="secondary" onClick={() => void downloadPackage()}>
          Download tolerance package
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
            {preview.changed ? 'Changes detected' : 'No changes'} · {preview.unchanged} unchanged
          </p>
          <dl className="kv">
            {preview.created.map((rule) => (
              <div key={`c-${scopeLabel(rule)}`} className="contents">
                <dt>Add</dt>
                <dd>
                  {scopeLabel(rule)}: within {rule.within_amount}, case above {rule.case_amount}
                </dd>
              </div>
            ))}
            {preview.updated.map((rule) => (
              <div key={`u-${scopeLabel(rule)}`} className="contents">
                <dt>Change</dt>
                <dd>
                  {scopeLabel(rule)}: {rule.current.within_amount} / {rule.current.case_amount} →{' '}
                  {rule.within_amount} / {rule.case_amount}
                </dd>
              </div>
            ))}
            {preview.retired.map((rule) => (
              <div key={`r-${scopeLabel(rule)}`} className="contents">
                <dt>Retire</dt>
                <dd>{scopeLabel(rule)}</dd>
              </div>
            ))}
          </dl>
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
              {busy ? 'Applying…' : 'Apply tolerance package'}
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
              <span className="page-sub">Finance role is required to apply tolerances.</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
