'use client';

import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useMe } from '@/components/MeProvider';
import { useToast } from '@/components/Feedback';
import { api } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import {
  Alert,
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  MetricGrid,
  MetricTile,
} from '@/components/ui';

type Role = 'admin' | 'finance' | 'it' | 'analyst' | 'sales_audit_manager' | 'auditor';
type BodyShape = 'wrapped' | 'spread';

interface PackageFlow {
  id: string;
  title: string;
  description: string;
  path: string;
  section: string;
  exportUrl: string;
  previewUrl: string;
  applyUrl: string;
  exportRoles: Role[];
  previewRoles: Role[];
  applyRoles: Role[];
  previewBody: BodyShape;
  applyBody: BodyShape;
  reasonRequired: boolean;
  maxBytes: number;
  filePrefix: string;
  control: string;
}

const PACKAGE_FLOWS: PackageFlow[] = [
  {
    id: 'source-profiles',
    title: 'Source feed profiles',
    description: 'Move source names, schedules, and column mappings between environments.',
    path: '/settings/integrations?tab=integrations',
    section: 'Inbound feeds',
    exportUrl: '/api/ingest/profiles/package',
    previewUrl: '/api/ingest/profiles/package/preview',
    applyUrl: '/api/ingest/profiles/package/apply',
    exportRoles: ['it', 'finance'],
    previewRoles: ['it'],
    applyRoles: ['it'],
    previewBody: 'wrapped',
    applyBody: 'wrapped',
    reasonRequired: true,
    maxBytes: 1_000_000,
    filePrefix: 'source-feed-profiles',
    control: 'Secrets are excluded; imported profiles remain disabled.',
  },
  {
    id: 'reconciliation-policy',
    title: 'Reconciliation policy',
    description: 'Promote approved matching tolerances and duplicate-detection settings.',
    path: '/settings/integrations?tab=reconciliation',
    section: 'Matching tolerance',
    exportUrl: '/api/reconciliation/policy/package',
    previewUrl: '/api/reconciliation/policy/package/preview',
    applyUrl: '/api/reconciliation/policy/package/apply',
    exportRoles: ['finance', 'it'],
    previewRoles: ['finance', 'it'],
    applyRoles: ['finance'],
    previewBody: 'wrapped',
    applyBody: 'wrapped',
    reasonRequired: true,
    maxBytes: 100_000,
    filePrefix: 'reconciliation-policy',
    control: 'Finance applies the reviewed changes with a reason.',
  },
  {
    id: 'system-options',
    title: 'System options',
    description: 'Transfer operational settings that affect future imports and processing.',
    path: '/settings/integrations?tab=import-policy',
    section: 'Import policy',
    exportUrl: '/api/system-options/package',
    previewUrl: '/api/system-options/package/preview',
    applyUrl: '/api/system-options/package/apply',
    exportRoles: ['it', 'admin'],
    previewRoles: ['it', 'admin'],
    applyRoles: ['it', 'admin'],
    previewBody: 'wrapped',
    applyBody: 'wrapped',
    reasonRequired: true,
    maxBytes: 100_000,
    filePrefix: 'system-options',
    control: 'Preview changes and apply with an audit reason.',
  },
  {
    id: 'voucher-policy',
    title: 'Voucher policy',
    description: 'Promote tender aging, retention, and jurisdiction-routing policy together.',
    path: '/settings/integrations?tab=voucher-policy',
    section: 'Voucher policy',
    exportUrl: '/api/vouchers/policy-package',
    previewUrl: '/api/vouchers/policy-package/preview',
    applyUrl: '/api/vouchers/policy-package/apply',
    exportRoles: ['it', 'admin'],
    previewRoles: ['it', 'admin'],
    applyRoles: ['it', 'admin'],
    previewBody: 'wrapped',
    applyBody: 'wrapped',
    reasonRequired: true,
    maxBytes: 500_000,
    filePrefix: 'voucher-policy',
    control: 'Preview changes and apply with an audit reason.',
  },
  {
    id: 'total-definitions',
    title: 'Total definitions',
    description: 'Export all revisions or import definitions as inactive worksheets.',
    path: '/settings/controls?tab=Totals%20%26%20rules',
    section: 'Totals & rules',
    exportUrl: '/api/total-definitions/package',
    previewUrl: '/api/total-definitions/package/preview',
    applyUrl: '/api/total-definitions/package/apply',
    exportRoles: ['finance', 'it'],
    previewRoles: ['finance', 'it'],
    applyRoles: ['finance'],
    previewBody: 'spread',
    applyBody: 'spread',
    reasonRequired: false,
    maxBytes: 2_000_000,
    filePrefix: 'audit-total-definitions',
    control: 'Imported definitions require the normal review and approval flow.',
  },
  {
    id: 'store-day-rules',
    title: 'Store-day audit rules',
    description: 'Exchange rule worksheets without activating them in the target environment.',
    path: '/settings/controls?tab=Totals%20%26%20rules',
    section: 'Totals & rules',
    exportUrl: '/api/audit-rules/package',
    previewUrl: '/api/audit-rules/package/preview',
    applyUrl: '/api/audit-rules/package/apply',
    exportRoles: ['finance', 'it', 'analyst', 'auditor'],
    previewRoles: ['finance'],
    applyRoles: ['finance'],
    previewBody: 'wrapped',
    applyBody: 'wrapped',
    reasonRequired: false,
    maxBytes: 2_000_000,
    filePrefix: 'audit-rules',
    control: 'Imported rules remain worksheets until separately submitted and approved.',
  },
  {
    id: 'transaction-audit-rules',
    title: 'Transaction audit rules',
    description: 'Move transaction conditions and error actions as a versioned rule set.',
    path: '/settings/controls?tab=Transaction%20rules',
    section: 'Transaction rules',
    exportUrl: '/api/transaction-audit-rules/package',
    previewUrl: '/api/transaction-audit-rules/import/preview',
    applyUrl: '/api/transaction-audit-rules/import/apply',
    exportRoles: ['finance', 'it', 'analyst', 'sales_audit_manager', 'auditor'],
    previewRoles: ['finance'],
    applyRoles: ['finance'],
    previewBody: 'spread',
    applyBody: 'spread',
    reasonRequired: false,
    maxBytes: 2_000_000,
    filePrefix: 'transaction-audit-rules',
    control: 'Imported rules are disabled worksheets and require separate review and approval.',
  },
  {
    id: 'gl-mappings',
    title: 'GL cross-references',
    description: 'Compare and apply create, update, and unchanged account mappings.',
    path: '/settings/controls?tab=GL%20mapping',
    section: 'GL mapping',
    exportUrl: '/api/gl/cross-references/package',
    previewUrl: '/api/gl/cross-references/package/preview',
    applyUrl: '/api/gl/cross-references/package/apply',
    exportRoles: ['finance', 'it'],
    previewRoles: ['finance', 'it'],
    applyRoles: ['finance'],
    previewBody: 'spread',
    applyBody: 'spread',
    reasonRequired: true,
    maxBytes: 2_000_000,
    filePrefix: 'gl-cross-references',
    control: 'Finance reviews a mapping diff before atomic, reasoned apply.',
  },
  {
    id: 'error-definitions',
    title: 'Sales Audit error definitions',
    description: 'Exchange custom error codes and their export-impact configuration.',
    path: '/settings/controls?tab=Error%20codes',
    section: 'Error codes',
    exportUrl: '/api/audit-errors/definitions/package',
    previewUrl: '/api/audit-errors/definitions/package/preview',
    applyUrl: '/api/audit-errors/definitions/package/apply',
    exportRoles: ['finance', 'it'],
    previewRoles: ['finance', 'it'],
    applyRoles: ['finance'],
    previewBody: 'spread',
    applyBody: 'spread',
    reasonRequired: true,
    maxBytes: 2_000_000,
    filePrefix: 'sales-audit-error-definitions',
    control: 'Built-in codes are protected; Finance applies reviewed changes.',
  },
  {
    id: 'variance-tolerances',
    title: 'Variance tolerance rules',
    description: 'Promote tolerance rules with create, change, and retire review.',
    path: '/settings/close',
    section: 'Tolerances & period close',
    exportUrl: '/api/variance-tolerances/package',
    previewUrl: '/api/variance-tolerances/package/preview',
    applyUrl: '/api/variance-tolerances/package/apply',
    exportRoles: ['finance', 'it', 'auditor'],
    previewRoles: ['finance', 'it'],
    applyRoles: ['finance'],
    previewBody: 'wrapped',
    applyBody: 'wrapped',
    reasonRequired: true,
    maxBytes: 500_000,
    filePrefix: 'variance-tolerances',
    control: 'Finance applies the reviewed changes.',
  },
  {
    id: 'tender-types',
    title: 'Tender types',
    description: 'Move accepted payment types, validation rules, and tender groups.',
    path: '/settings/tenders',
    section: 'Tender types',
    exportUrl: '/api/tender-types/package',
    previewUrl: '/api/tender-types/package/preview',
    applyUrl: '/api/tender-types/package/apply',
    exportRoles: ['analyst', 'finance', 'it', 'auditor'],
    previewRoles: ['finance', 'it'],
    applyRoles: ['finance', 'it'],
    previewBody: 'wrapped',
    applyBody: 'wrapped',
    reasonRequired: true,
    maxBytes: 500_000,
    filePrefix: 'tender-types',
    control: 'Preview changes before applying named tender types.',
  },
];

const FOUNDATION_FLOW = {
  title: 'Foundation data',
  description: 'Upload validated store, location, tender, and related reference data.',
  path: '/foundation',
  control: 'Use the validated CSV tools in the foundation data workspace.',
};

interface ImportPreview {
  fileName: string;
  bundle: Record<string, unknown>;
  result: Record<string, unknown>;
  token: string;
  valid: boolean;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const roleCan = (role: string | undefined, roles: Role[]) =>
  role === 'admin' || (!!role && roles.includes(role as Role));
const sizeLabel = (bytes: number) =>
  bytes >= 1_000_000 ? `${bytes / 1_000_000} MB` : `${bytes / 1000} KB`;

const apiMessage = (error: unknown, fallback: string) => {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((item) => JSON.stringify(item)).join('; ');
  return fallback;
};
const previewErrorText = (value: unknown) =>
  isRecord(value) ? JSON.stringify(value) : String(value);

export default function DataExchangePage() {
  const { me } = useMe();
  const toast = useToast();
  const workbenchRef = useRef<HTMLDivElement>(null);
  const packageInput = useRef<HTMLInputElement>(null);
  const [activeId, setActiveId] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const activeFlow = PACKAGE_FLOWS.find((flow) => flow.id === activeId) ?? null;
  const allowedPaths = useMemo(
    () =>
      new Set(
        (me?.screens ?? []).filter((screen) => screen.allowed).map((screen) => screen.path)
      ),
    [me?.screens]
  );
  const packageFlows = PACKAGE_FLOWS.filter((flow) => {
    const path = flow.path.split('?')[0];
    const role = me?.role === 'sales_audit_manager' ? 'analyst' : me?.role;
    return (
      allowedPaths.has(path) &&
      (roleCan(role, flow.exportRoles) || roleCan(role, flow.previewRoles))
    );
  });
  const effectiveRole = me?.role === 'sales_audit_manager' ? 'analyst' : me?.role;
  const canPreview = !!activeFlow && roleCan(effectiveRole, activeFlow.previewRoles);
  const canApply = !!activeFlow && roleCan(effectiveRole, activeFlow.applyRoles);

  useEffect(() => {
    if (activeFlow) workbenchRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [activeFlow]);

  const selectFlow = (flow: PackageFlow) => {
    setActiveId(flow.id);
    setFile(null);
    if (packageInput.current) packageInput.current.value = '';
    setPreview(null);
    setReason('');
    setError('');
  };

  const downloadPackage = async (flow: PackageFlow) => {
    setBusy(true);
    setError('');
    try {
      const { data } = await api.get(flow.exportUrl);
      const date = new Date().toISOString().slice(0, 10);
      downloadBlob(
        `${flow.filePrefix}-${date}.json`,
        new Blob([JSON.stringify(data, null, 2)], {
          type: 'application/json;charset=utf-8',
        })
      );
      toast(`${flow.title} package downloaded.`, 'success');
    } catch (cause) {
      setError(apiMessage(cause, `Could not download the ${flow.title.toLowerCase()} package.`));
    } finally {
      setBusy(false);
    }
  };

  const previewPackage = async () => {
    if (!activeFlow || !file || !canPreview) return;
    setBusy(true);
    setError('');
    setPreview(null);
    try {
      if (file.size > activeFlow.maxBytes) {
        throw new Error(`This package must be smaller than ${sizeLabel(activeFlow.maxBytes)}.`);
      }
      const parsed: unknown = JSON.parse(await file.text());
      if (!isRecord(parsed)) throw new Error('The package must contain a JSON object.');
      const body = activeFlow.previewBody === 'wrapped' ? { package: parsed } : parsed;
      const { data } = await api.post(activeFlow.previewUrl, body);
      const result = isRecord(data?.result) ? data.result : data;
      const token = typeof result?.preview_token === 'string' ? result.preview_token : '';
      setPreview({
        fileName: file.name,
        bundle: parsed,
        result,
        token,
        valid: result?.valid === true && !!token,
      });
      setReason('');
    } catch (cause) {
      setError(
        cause instanceof SyntaxError
          ? 'The selected file is not valid JSON.'
          : cause instanceof Error && !('response' in cause)
            ? cause.message
            : apiMessage(cause, `Could not preview the ${activeFlow.title.toLowerCase()} package.`)
      );
    } finally {
      setBusy(false);
    }
  };

  const applyPackage = async () => {
    if (!activeFlow || !preview?.valid || !canApply) return;
    if (activeFlow.reasonRequired && reason.trim().length < 10) {
      setError('Enter a reason with at least 10 characters before applying this package.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const fields = {
        preview_token: preview.token,
        ...(activeFlow.reasonRequired ? { reason: reason.trim() } : {}),
      };
      const body =
        activeFlow.applyBody === 'wrapped'
          ? { package: preview.bundle, ...fields }
          : { ...preview.bundle, ...fields };
      await api.post(activeFlow.applyUrl, body);
      setPreview(null);
      setFile(null);
      if (packageInput.current) packageInput.current.value = '';
      setReason('');
      toast(`${activeFlow.title} package applied.`, 'success');
    } catch (cause) {
      setError(
        apiMessage(
          cause,
          `Could not apply the ${activeFlow.title.toLowerCase()} package. Preview again and retry.`
        )
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack stack-md">
      <Card title="Configuration and foundation data exchange">
        <p>
          Export, validate, preview, and apply supported retail configuration packages from one
          workspace. Each domain still enforces its own permissions, stale-preview checks, approval
          policy, and audit record.
        </p>
        <div className="cluster">
          <Badge variant="info">{packageFlows.length} available packages</Badge>
          <span className="hint">
            Package access follows your assigned role and screen permissions.
          </span>
        </div>
      </Card>

      {packageFlows.length || allowedPaths.has('/foundation') ? (
        <div className="cc-grid">
          {packageFlows.map((flow) => {
            const exportAllowed = roleCan(effectiveRole, flow.exportRoles);
            const previewAllowed = roleCan(effectiveRole, flow.previewRoles);
            return (
              <Card key={flow.id} title={flow.title}>
                <div className="stack stack-sm">
                  <div className="cluster">
                    <Badge variant="secondary">JSON package</Badge>
                    <span className="hint">{flow.section}</span>
                  </div>
                  <p>{flow.description}</p>
                  <p className="hint">{flow.control}</p>
                  <div className="cluster">
                    {exportAllowed && (
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={busy}
                        onClick={() => void downloadPackage(flow)}
                      >
                        Download package
                      </Button>
                    )}
                    {previewAllowed && (
                      <Button size="sm" onClick={() => selectFlow(flow)}>
                        Import package
                      </Button>
                    )}
                    <Link href={flow.path} className="btn-secondary btn-sm">
                      Open settings
                    </Link>
                  </div>
                </div>
              </Card>
            );
          })}
          {allowedPaths.has('/foundation') && (
            <Card title={FOUNDATION_FLOW.title}>
              <div className="stack stack-sm">
                <Badge variant="secondary">CSV data</Badge>
                <p>{FOUNDATION_FLOW.description}</p>
                <p className="hint">{FOUNDATION_FLOW.control}</p>
                <div>
                  <Link href={FOUNDATION_FLOW.path} className="btn-secondary btn-sm">
                    Open workflow
                  </Link>
                </div>
              </div>
            </Card>
          )}
        </div>
      ) : (
        <EmptyState title="No exchange workflows available">
          Your current role does not have access to configuration or foundation data exchange.
        </EmptyState>
      )}

      {error && (
        <Alert tone="error" onDismiss={() => setError('')}>
          {error}
        </Alert>
      )}

      {activeFlow && (
        <div ref={workbenchRef}>
          <Card title={`Import ${activeFlow.title}`}>
            <div className="stack stack-md">
              <p>
                Select a versioned JSON package to validate against the current environment. Applying
                a previewed package preserves the domain approval rules shown below.
              </p>
              <div className="cluster">
                <Badge variant="secondary">{activeFlow.section}</Badge>
                <span className="hint">
                  Maximum file size: {sizeLabel(activeFlow.maxBytes)}
                </span>
              </div>
              <Field label="Package file">
                <input
                  ref={packageInput}
                  type="file"
                  accept="application/json,.json"
                  disabled={busy || !canPreview}
                  onChange={(event) => {
                    setFile(event.currentTarget.files?.[0] ?? null);
                    setPreview(null);
                    setReason('');
                    setError('');
                  }}
                />
              </Field>
              {file && (
                <p className="hint">
                  Selected: {file.name} · {file.size.toLocaleString()} bytes
                </p>
              )}
              <p className="hint">{activeFlow.control}</p>
              <div className="cluster">
                <Button
                  disabled={!file || busy || !canPreview}
                  onClick={() => void previewPackage()}
                >
                  {busy ? 'Working…' : 'Validate and preview'}
                </Button>
                <Button variant="secondary" disabled={busy} onClick={() => selectFlow(activeFlow)}>
                  Clear
                </Button>
              </div>

              {preview && (
                <section className="stack stack-sm" aria-live="polite">
                  <div className="cluster">
                    <h3>Preview: {preview.fileName}</h3>
                    <Badge variant={preview.valid ? 'success' : 'error'}>
                      {preview.valid ? 'Valid preview' : 'Needs attention'}
                    </Badge>
                  </div>
                  <p className="hint">
                    This preview is bound to the current target configuration. If the target changes,
                    the server will reject a stale apply and require a fresh preview.
                  </p>
                  {isRecord(preview.result.counts) && (
                    <MetricGrid>
                      {Object.entries(preview.result.counts).map(([key, value]) => (
                        <MetricTile
                          key={key}
                          label={key.replaceAll('_', ' ')}
                          value={typeof value === 'string' || typeof value === 'number' ? value : '—'}
                        />
                      ))}
                    </MetricGrid>
                  )}
                  {Array.isArray(preview.result.errors) && preview.result.errors.length > 0 && (
                    <Alert tone="error">
                      <strong>{preview.result.errors.length} validation issue(s)</strong>
                      <ul>
                        {preview.result.errors.slice(0, 10).map((item, index) => (
                          <li key={index}>{previewErrorText(item)}</li>
                        ))}
                      </ul>
                      {preview.result.errors.length > 10 && (
                        <p className="hint">Open the complete details below to inspect the rest.</p>
                      )}
                    </Alert>
                  )}
                  <details>
                    <summary>Inspect complete validation and change details</summary>
                    <pre style={{ maxHeight: '28rem', overflow: 'auto', whiteSpace: 'pre-wrap' }}>
                      {JSON.stringify(preview.result, null, 2)}
                    </pre>
                  </details>
                  {activeFlow.reasonRequired && canApply && (
                    <Field label="Reason for applying this package">
                      <textarea
                        value={reason}
                        maxLength={2000}
                        rows={3}
                        onChange={(event) => setReason(event.target.value)}
                        disabled={busy}
                      />
                    </Field>
                  )}
                  {!canApply && preview.valid && (
                    <p className="hint">Your role can preview this package but cannot apply it.</p>
                  )}
                  <div>
                    <Button
                      disabled={
                        busy ||
                        !preview.valid ||
                        preview.result.changed === false ||
                        !canApply ||
                        (activeFlow.reasonRequired && reason.trim().length < 10)
                      }
                      onClick={() => void applyPackage()}
                    >
                      {busy ? 'Applying…' : 'Apply reviewed package'}
                    </Button>
                  </div>
                </section>
              )}
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
