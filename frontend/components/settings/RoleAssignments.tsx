'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
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

function errorMessage(cause: unknown, fallback: string) {
  const detail = (cause as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
}

/** Ontology Studio says who a person is; this is where Nimbus gives them a business role. */
export function RoleAssignments() {
  const [data, setData] = useState<Listing | null>(null);
  const [q, setQ] = useState('');
  const [assigned, setAssigned] = useState<'all' | 'assigned' | 'unassigned'>('all');
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<Person | null>(null);
  const [role, setRole] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Listing>('/api/access/role-assignments', {
        params: { q: q.trim() || undefined, assigned, page, page_size: 25 },
      });
      setData(data);
      setError(null);
    } catch (cause) {
      setError(errorMessage(cause, 'Could not load the role assignments.'));
    }
  }, [q, assigned, page]);

  useEffect(() => {
    void load();
  }, [load]);

  const label = (key: string | null) =>
    data?.roles.find((item) => item.key === key)?.label ?? key ?? '—';

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

  return (
    <SettingsSection
      title="Business roles"
      description="Ontology Studio decides who someone is and whether they are an administrator, but it cannot express Finance, IT, auditor or store roles. Give each person who has signed in the role they hold here. Studio administrators keep their Studio role. Changes are audited and apply on the person's next request."
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
                setAssigned(event.target.value as typeof assigned);
                setPage(1);
              }}
            >
              <option value="all">Everyone</option>
              <option value="unassigned">Without an assigned role</option>
              <option value="assigned">With an assigned role</option>
            </select>
          </Field>
        </div>
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
