'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { useToast } from '@/components/Feedback';
import { useMe } from '@/components/MeProvider';
import { Alert, Button, Card, DataTable, Field } from '@/components/ui';

type ErrorDefinition = {
  code: string;
  description: string;
  short_description: string;
  form: string | null;
  tab: string | null;
  recommended_solution: string;
  active: boolean;
  export_impact_configured: boolean;
  export_block_destinations: string[];
};

type ErrorPackage = {
  format: 'zetsa.sales-audit-error-catalog';
  version: 1;
  definitions: ErrorDefinition[];
};

type Preview = {
  valid: boolean;
  preview_token: string | null;
  errors: { row: number; message: string }[];
  counts: { create: number; update: number; unchanged: number };
  definitions: (ErrorDefinition & { row: number; action: string })[];
};

const errorMessage = (cause: unknown, fallback: string) => {
  const detail = (cause as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const issue = item as { msg?: string };
        return issue.msg ?? JSON.stringify(item);
      })
      .join('; ');
  }
  if (detail && typeof detail === 'object' && 'errors' in detail) {
    const errors = (detail as { errors?: { row?: number; message?: string }[] }).errors;
    if (errors?.length) return errors.map((item) => `Row ${item.row}: ${item.message}`).join('; ');
  }
  return fallback;
};

export function ErrorDefinitionPackages() {
  const { me } = useMe();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [definitions, setDefinitions] = useState<ErrorDefinition[]>([]);
  const [preview, setPreview] = useState<{
    fileName: string;
    bundle: ErrorPackage;
    result: Preview;
  } | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canApply = me?.role === 'finance' || me?.role === 'admin';

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<ErrorDefinition[]>('/api/audit-errors/definitions');
      setDefinitions(data);
      setError(null);
    } catch (cause) {
      setError(errorMessage(cause, 'Could not load Sales Audit error definitions.'));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const downloadPackage = async () => {
    try {
      const { data } = await api.get<ErrorPackage>('/api/audit-errors/definitions/package');
      downloadBlob(
        `sales-audit-error-catalog-${new Date().toISOString().slice(0, 10)}.json`,
        new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' })
      );
      toast('Sales Audit error catalog downloaded.', 'success');
    } catch (cause) {
      setError(errorMessage(cause, 'Could not download the error catalog.'));
    }
  };

  const previewPackage = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    setPreview(null);
    try {
      if (file.size > 2_000_000) throw new Error('Catalog snapshots must be smaller than 2 MB.');
      const bundle = JSON.parse(await file.text()) as ErrorPackage;
      const { data } = await api.post<Preview>(
        '/api/audit-errors/definitions/package/preview',
        bundle
      );
      setPreview({ fileName: file.name, bundle, result: data });
      setReason('');
    } catch (cause) {
      setError(
        cause instanceof SyntaxError
          ? 'The selected error catalog is not valid JSON.'
          : cause instanceof Error && !('response' in cause)
            ? cause.message
            : errorMessage(cause, 'Could not preview this error catalog.')
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
      const { data } = await api.post('/api/audit-errors/definitions/package/apply', {
        ...preview.bundle,
        preview_token: preview.result.preview_token,
        reason,
      });
      setPreview(null);
      setReason('');
      await load();
      toast(
        `Catalog applied: ${data.created} created, ${data.updated} updated, ${data.unchanged} unchanged.`,
        'success'
      );
    } catch (cause) {
      setError(errorMessage(cause, 'Could not apply the catalog. Preview it again and retry.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Sales Audit error catalog">
      <p>
        Download and edit the versioned catalog to maintain error descriptions, resolution steps,
        active state, and downstream export impact. New active definitions become selectable in
        transaction audit rules. Import preview shows every change before Finance applies it.
      </p>
      {error && (
        <Alert tone="error" onDismiss={() => setError(null)}>
          {error}
        </Alert>
      )}
      <div className="cluster mt-sm">
        <Button size="sm" variant="secondary" onClick={() => void downloadPackage()}>
          Download error catalog
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={busy || !canApply}
          onClick={() => input.current?.click()}
        >
          {busy ? 'Preparing import…' : 'Import catalog'}
        </Button>
        <input
          ref={input}
          type="file"
          accept=".json,application/json"
          className="sr-only"
          aria-label="Choose a Sales Audit error catalog snapshot"
          onChange={(event) => void previewPackage(event.currentTarget.files?.[0])}
        />
      </div>
      {preview && (
        <div className="stack-sm mt-md">
          <h3>Import preview: {preview.fileName}</h3>
          <p>
            {preview.result.counts.create} definition(s) will be created,{' '}
            {preview.result.counts.update} updated, and {preview.result.counts.unchanged} left
            unchanged. Codes omitted from the file remain unchanged.
          </p>
          {preview.result.errors.length > 0 && (
            <Alert tone="error">
              {preview.result.errors.map((item) => (
                <div key={`${item.row}-${item.message}`}>
                  Row {item.row}: {item.message}
                </div>
              ))}
            </Alert>
          )}
          <DataTable
            rows={preview.result.definitions}
            rowKey={(row) => `${row.row}-${row.code}`}
            columns={[
              { key: 'code', header: 'Code', primary: true, render: (row) => row.code },
              { key: 'label', header: 'Description', render: (row) => row.short_description },
              {
                key: 'form',
                header: 'Form / tab',
                render: (row) => [row.form, row.tab].filter(Boolean).join(' · ') || '—',
              },
              {
                key: 'status',
                header: 'Status',
                render: (row) => (row.active ? 'Active' : 'Inactive'),
              },
              {
                key: 'impact',
                header: 'Export impact',
                render: (row) =>
                  row.export_impact_configured
                    ? row.export_block_destinations.join(', ') || 'Informational'
                    : 'Default policy',
              },
              { key: 'action', header: 'Action', render: (row) => row.action },
            ]}
          />
          <Field
            label="Reason for import"
            hint="At least 10 characters; recorded in the audit trail."
          >
            <textarea
              value={reason}
              maxLength={4000}
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
              {busy ? 'Applying…' : 'Apply catalog'}
            </Button>
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => setPreview(null)}>
              Cancel
            </Button>
            {!canApply && (
              <span className="page-sub">Finance role is required to apply catalog changes.</span>
            )}
          </div>
        </div>
      )}
      <div className="mt-md">
        <DataTable
          rows={definitions}
          rowKey={(row) => row.code}
          emptyTitle="No error definitions are configured."
          columns={[
            { key: 'code', header: 'Code', primary: true, render: (row) => row.code },
            { key: 'short', header: 'Description', render: (row) => row.short_description },
            {
              key: 'form',
              header: 'Form / tab',
              render: (row) => [row.form, row.tab].filter(Boolean).join(' · ') || '—',
            },
            {
              key: 'status',
              header: 'Status',
              render: (row) => (row.active ? 'Active' : 'Inactive'),
            },
            {
              key: 'solution',
              header: 'Resolution guidance',
              render: (row) => row.recommended_solution,
            },
          ]}
        />
      </div>
    </Card>
  );
}
