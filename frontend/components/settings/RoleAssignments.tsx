'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { downloadBlob, downloadCsv } from '@/lib/csv';
import { formatDateTime } from '@/lib/preferences';
import { SkeletonRows } from '@/components/Feedback';
import { Alert, Button, Card, Field } from '@/components/ui';
import { SettingsSection } from '@/components/settings/SettingsSection';

type Person = {
  assignment_identifier: string;
  user_identifier: string;
  display_name: string;
  last_seen_at: string;
  signed_in_role: string;
  assigned_role: string | null;
  assigned_by: string | null;
  assigned_at: string | null;
  reason: string | null;
};

type Listing = {
  roles: { key: string; label: string }[];
  items: Person[];
  pagination: { page: number; page_size: number; total: number; pages: number };
};

type RoleImportPreview = {
  valid: boolean;
  preview_token?: string;
  counts: Record<'Create' | 'Replace' | 'Unchanged', number>;
  rows: {
    row: number;
    assignment_identifier: string;
    display_name: string;
    user_identifier: string;
    signed_in_role: string;
    current_role: string | null;
    role: string;
    reason: string;
    action: string;
  }[];
  errors: { row: number; message: string }[];
  errors_truncated: boolean;
};

function errorMessage(cause: unknown, fallback: string) {
  const detail = (cause as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
}

/** Ontology Studio says who a person is; this is where Nimbus gives them a business role. */
export function RoleAssignments() {
  const [data, setData] = useState<Listing | null>(null);
  const [q, setQ] = useState('');
  const [assigned, setAssigned] = useState<'all' | 'assigned' | 'unassigned'>('all');
  const [assignedRole, setAssignedRole] = useState('');
  const [inactiveDays, setInactiveDays] = useState('');
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<Person | null>(null);
  const [role, setRole] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [roleImportPreview, setRoleImportPreview] = useState<RoleImportPreview | null>(null);
  const [roleImportCsv, setRoleImportCsv] = useState<string | null>(null);
  const [roleImportBusy, setRoleImportBusy] = useState(false);
  const roleImportFile = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportNotice, setExportNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Listing>('/api/access/role-assignments', {
        params: {
          q: q.trim() || undefined,
          assigned,
          assigned_role: assignedRole || undefined,
          inactive_days: inactiveDays || undefined,
          page,
          page_size: 25,
        },
      });
      setData(data);
      setError(null);
    } catch (cause) {
      setError(errorMessage(cause, 'Could not load the role assignments.'));
    }
  }, [q, assigned, assignedRole, inactiveDays, page]);

  useEffect(() => {
    void load();
  }, [load]);

  const label = (key: string | null) =>
    data?.roles.find((item) => item.key === key)?.label ?? key ?? '—';

  const exportMatching = async () => {
    setExporting(true);
    setExportNotice(null);
    try {
      const params = new URLSearchParams({ format: 'csv' });
      if (q.trim()) params.set('q', q.trim());
      if (assigned !== 'all') params.set('assigned', assigned);
      if (assignedRole) params.set('assigned_role', assignedRole);
      if (inactiveDays) params.set('inactive_days', inactiveDays);
      const response = await api.get<Blob>(`/api/access/role-assignments?${params}`, {
        responseType: 'blob',
      });
      downloadBlob('business-role-assignments.csv', response.data);
      if (response.headers?.['x-role-assignments-truncated'] === 'true') {
        setExportNotice('Export includes the first 50,000 matching role assignments.');
      }
    } catch (cause) {
      setError(errorMessage(cause, 'Could not export the role assignments.'));
    } finally {
      setExporting(false);
    }
  };

  const edit = (person: Person) => {
    setEditing(person);
    setRole(person.assigned_role ?? '');
    setReason('');
    setNotice(null);
  };

  const save = async () => {
    if (!editing || !role) return;
    setBusy(true);
    setError(null);
    try {
      await api.put('/api/access/role-assignments', {
        assignment_identifier: editing.assignment_identifier,
        role,
        reason,
      });
      setNotice(`${editing.display_name} now holds the ${label(role)} role.`);
      setEditing(null);
      await load();
    } catch (cause) {
      setError(errorMessage(cause, 'Could not save the role.'));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!editing) return;
    setBusy(true);
    setError(null);
    try {
      await api.delete(
        `/api/access/role-assignments/${encodeURIComponent(editing.assignment_identifier)}`,
        { params: { reason } }
      );
      setNotice(`${editing.display_name} goes back to their Ontology Studio role.`);
      setEditing(null);
      await load();
    } catch (cause) {
      setError(errorMessage(cause, 'Could not remove the role.'));
    } finally {
      setBusy(false);
    }
  };

  const previewRoleImport = async (file?: File) => {
    if (!file) return;
    setError(null);
    setNotice(null);
    setRoleImportBusy(true);
    try {
      const csvText = await file.text();
      const { data } = await api.post<RoleImportPreview>(
        '/api/access/role-assignments/import/preview',
        { csv_text: csvText }
      );
      setRoleImportCsv(csvText);
      setRoleImportPreview(data);
    } catch (cause) {
      setError(errorMessage(cause, 'Could not preview the role assignment CSV.'));
    } finally {
      setRoleImportBusy(false);
    }
  };

  const applyRoleImport = async () => {
    if (!roleImportPreview?.valid || !roleImportPreview.preview_token || !roleImportCsv) return;
    setRoleImportBusy(true);
    setError(null);
    try {
      const { data } = await api.post<{ applied: Record<string, number> }>(
        '/api/access/role-assignments/import',
        { csv_text: roleImportCsv, preview_token: roleImportPreview.preview_token }
      );
      const counts = data.applied;
      setNotice(
        `Role import applied: ${counts.created} created, ${counts.replaced} changed, ${counts.unchanged} unchanged.`
      );
      setRoleImportPreview(null);
      setRoleImportCsv(null);
      setPage(1);
      await load();
    } catch (cause) {
      setError(
        errorMessage(cause, 'Could not apply the role assignment CSV. Preview the file again.')
      );
    } finally {
      setRoleImportBusy(false);
    }
  };

  return (
    <SettingsSection
      title="Business roles"
      description="Ontology Studio decides who someone is and whether they are an administrator, but it cannot express Finance, IT, Sales Auditor, internal auditor, or store roles. Give each person who has signed in the role they hold here. Studio administrators keep their Studio role. Changes are audited and apply on the person's next request."
    >
      {error && <Alert tone="error">{error}</Alert>}
      {notice && <Alert tone="success">{notice}</Alert>}
      <Card flush title="People who have signed in">
        <div className="form-row">
          <div className="grow">
            <Field label="Search people">
              <input
                value={q}
                onChange={(event) => {
                  setQ(event.target.value);
                  setPage(1);
                }}
                placeholder="Name or email"
              />
            </Field>
          </div>
          <Field label="Show">
            <select
              value={assigned}
              onChange={(event) => {
                const next = event.target.value as typeof assigned;
                setAssigned(next);
                if (next === 'unassigned') setAssignedRole('');
                setPage(1);
              }}
            >
              <option value="all">Everyone</option>
              <option value="unassigned">Without an assigned role</option>
              <option value="assigned">With an assigned role</option>
            </select>
          </Field>
          <Field label="Business role">
            <select
              value={assignedRole}
              disabled={assigned === 'unassigned'}
              onChange={(event) => {
                setAssignedRole(event.target.value);
                if (event.target.value) setAssigned('assigned');
                setPage(1);
              }}
            >
              <option value="">Any role</option>
              {data?.roles.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Last seen">
            <select
              value={inactiveDays}
              onChange={(event) => {
                setInactiveDays(event.target.value);
                setPage(1);
              }}
            >
              <option value="">Any time</option>
              <option value="30">Not seen for 30+ days</option>
              <option value="90">Not seen for 90+ days</option>
              <option value="180">Not seen for 180+ days</option>
            </select>
          </Field>
          <div className="action-buttons">
            <Button
              size="sm"
              variant="secondary"
              disabled={exporting || !data?.pagination.total}
              onClick={() => void exportMatching()}
            >
              {exporting ? 'Exporting…' : 'Export matching CSV'}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() =>
                downloadCsv(
                  'business-role-template.csv',
                  ['assignment_identifier', 'role', 'reason'],
                  []
                )
              }
            >
              Download CSV template
            </Button>
            <input
              ref={roleImportFile}
              type="file"
              accept=".csv,text/csv"
              hidden
              onChange={(event) => {
                void previewRoleImport(event.target.files?.[0]);
                event.target.value = '';
              }}
            />
            <Button
              size="sm"
              variant="secondary"
              disabled={roleImportBusy}
              onClick={() => roleImportFile.current?.click()}
            >
              {roleImportBusy ? 'Preparing…' : 'Import role CSV'}
            </Button>
          </div>
        </div>
        {inactiveDays && (
          <p className="hint">
            Inactivity is a review signal only. Confirm the person’s status in Ontology Studio
            before removing an assigned business role.
          </p>
        )}
        {exportNotice && <Alert tone="warning">{exportNotice}</Alert>}
        {!data ? (
          <SkeletonRows rows={4} />
        ) : data.items.length === 0 ? (
          <p className="cc-empty">
            No one matches. People appear here after their first sign-in through Ontology Studio.
          </p>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Person</th>
                  <th>Identity ID</th>
                  <th>Signed in as</th>
                  <th>Assigned role</th>
                  <th>Last seen</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {data.items.map((person) => (
                  <tr key={person.assignment_identifier}>
                    <td>
                      <b>{person.display_name}</b>
                      <div className="hint">{person.user_identifier}</div>
                    </td>
                    <td>
                      <code>{person.assignment_identifier}</code>
                    </td>
                    <td>{label(person.signed_in_role)}</td>
                    <td>
                      {person.assigned_role ? (
                        <>
                          {label(person.assigned_role)}
                          <div className="hint" title={person.reason ?? ''}>
                            by {person.assigned_by}
                          </div>
                        </>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td>{formatDateTime(person.last_seen_at)}</td>
                    <td>
                      <Button size="sm" onClick={() => edit(person)}>
                        {person.assigned_role ? 'Change…' : 'Assign…'}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.pagination.pages > 1 && (
          <div className="cluster">
            <Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              Previous
            </Button>
            <span className="hint">
              Page {data.pagination.page} of {data.pagination.pages}
            </span>
            <Button
              size="sm"
              disabled={page >= data.pagination.pages}
              onClick={() => setPage(page + 1)}
            >
              Next
            </Button>
          </div>
        )}
      </Card>
      {roleImportPreview && (
        <Card title="Business role import preview">
          <div className="stack stack-sm">
            <p>
              {roleImportPreview.counts.Create} new · {roleImportPreview.counts.Replace} changed ·{' '}
              {roleImportPreview.counts.Unchanged} unchanged. All rows are validated before any role
              is changed.
            </p>
            {roleImportPreview.errors.length > 0 && (
              <Alert tone="error">
                {roleImportPreview.errors
                  .slice(0, 10)
                  .map((item) => `Row ${item.row}: ${item.message}`)
                  .join(' · ')}
                {roleImportPreview.errors_truncated && ' · Additional errors omitted.'}
              </Alert>
            )}
            {roleImportPreview.valid && (
              <>
                <p className="hint">
                  Previewing {roleImportPreview.rows.length} rows. The complete file is validated
                  and will apply atomically.
                </p>
                <div className="table-scroll">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>CSV row</th>
                        <th>Person</th>
                        <th>Identity ID</th>
                        <th>Current role</th>
                        <th>New role</th>
                        <th>Reason</th>
                        <th>Change</th>
                      </tr>
                    </thead>
                    <tbody>
                      {roleImportPreview.rows.slice(0, 10).map((row) => (
                        <tr key={`${row.row}-${row.assignment_identifier}`}>
                          <td>{row.row}</td>
                          <td>{row.display_name || row.user_identifier || 'Unknown identity'}</td>
                          <td>
                            <code>{row.assignment_identifier}</code>
                          </td>
                          <td>{label(row.current_role)}</td>
                          <td>{label(row.role)}</td>
                          <td title={row.reason}>
                            {row.reason.length > 100 ? `${row.reason.slice(0, 100)}…` : row.reason}
                          </td>
                          <td>{row.action}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
            <p className="hint">
              CSV headers: assignment_identifier, role, reason. Use an identity ID from the
              directory, a role key such as finance or store_staff, and a reason of at least 10
              characters. Studio administrators cannot receive an assigned business role.
            </p>
            <div className="cluster">
              <Button
                variant="primary"
                disabled={!roleImportPreview.valid || roleImportBusy}
                onClick={() => void applyRoleImport()}
              >
                {roleImportBusy ? 'Applying…' : 'Apply all role changes'}
              </Button>
              <Button
                disabled={roleImportBusy}
                onClick={() => {
                  setRoleImportPreview(null);
                  setRoleImportCsv(null);
                }}
              >
                Discard preview
              </Button>
            </div>
          </div>
        </Card>
      )}
      {editing && data && (
        <Card title={`Business role for ${editing.display_name}`}>
          <div className="stack stack-sm">
            <Field label="Role">
              <select value={role} onChange={(event) => setRole(event.target.value)}>
                <option value="">Choose a role…</option>
                {data.roles.map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.label}
                  </option>
                ))}
              </select>
            </Field>
            {role === 'sales_audit_manager' && (
              <p className="hint">
                Coordinates auditor workload and can assign cases to verified Sales Auditors.
                Finance decisions still require a Finance role.
              </p>
            )}
            <Field label="Reason" hint="At least 10 characters; recorded in the audit trail.">
              <textarea
                value={reason}
                maxLength={2000}
                onChange={(event) => setReason(event.target.value)}
              />
            </Field>
            <div className="cluster">
              <Button
                variant="primary"
                disabled={busy || !role || reason.trim().length < 10}
                onClick={() => void save()}
              >
                {busy ? 'Saving…' : 'Save role'}
              </Button>
              {editing.assigned_role && (
                <Button disabled={busy || reason.trim().length < 10} onClick={() => void remove()}>
                  Remove assigned role
                </Button>
              )}
              <Button disabled={busy} onClick={() => setEditing(null)}>
                Cancel
              </Button>
            </div>
          </div>
        </Card>
      )}
    </SettingsSection>
  );
}
