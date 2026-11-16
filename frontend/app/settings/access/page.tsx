'use client';

import { Fragment, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { downloadBlob, downloadCsv } from '@/lib/csv';
import { formatDateTime } from '@/lib/preferences';
import { SkeletonRows } from '@/components/Feedback';
import { Alert, Button, Card, Field } from '@/components/ui';
import { useMe } from '@/components/MeProvider';
import { SettingsSection } from '@/components/settings/SettingsSection';

interface Matrix {
  roles: { key: string; label: string }[];
  screens: { key: string; path: string; label: string; group: string; roles: string[] }[];
  you: string;
}
interface Assignment {
  id: string;
  user_identifier: string;
  store_ids: string[];
  location_traits: string[];
  updated_by: string;
  updated_at: string;
}
interface VerifiedStoreUser {
  assignment_identifier: string;
  user_identifier: string;
  identity_id: string | null;
  display_name: string;
  role: string;
  first_seen_at: string;
  last_seen_at: string;
  has_local_scope: boolean;
}
interface AssignmentImportPreview {
  valid: boolean;
  preview_token?: string;
  counts: Record<'Create' | 'Replace' | 'Unchanged', number>;
  rows: {
    row: number;
    user_identifier: string;
    store_ids: string[];
    location_traits: string[];
    action: string;
  }[];
  errors: { row: number; message: string }[];
  errors_truncated: boolean;
}

export default function AccessPage() {
  const { me } = useMe();
  const [m, setM] = useState<Matrix | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [assignments, setAssignments] = useState<Assignment[] | null>(null);
  const [assignmentTotal, setAssignmentTotal] = useState(0);
  const [assignmentPages, setAssignmentPages] = useState(1);
  const [assignmentPage, setAssignmentPage] = useState(1);
  const [assignmentLoading, setAssignmentLoading] = useState(false);
  const [assignmentExporting, setAssignmentExporting] = useState(false);
  const [assignmentRefresh, setAssignmentRefresh] = useState(0);
  const [verifiedUsers, setVerifiedUsers] = useState<VerifiedStoreUser[]>([]);
  const [stores, setStores] = useState<string[]>([]);
  const [locationTraits, setLocationTraits] = useState<string[]>([]);
  const [userIdentifier, setUserIdentifier] = useState('');
  const [selectedStores, setSelectedStores] = useState<string[]>([]);
  const [selectedTraits, setSelectedTraits] = useState<string[]>([]);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [assignmentError, setAssignmentError] = useState<string | null>(null);
  const [assignmentNotice, setAssignmentNotice] = useState<string | null>(null);
  const [assignmentQuery, setAssignmentQuery] = useState('');
  const [directoryQuery, setDirectoryQuery] = useState('');
  const [directoryRole, setDirectoryRole] = useState<'all' | 'store_manager' | 'store_staff'>(
    'all'
  );
  const [directoryScope, setDirectoryScope] = useState<'all' | 'assigned' | 'unassigned'>('all');
  const [directoryPage, setDirectoryPage] = useState(1);
  const [directoryTotal, setDirectoryTotal] = useState(0);
  const [directoryPages, setDirectoryPages] = useState(1);
  const [directoryLoading, setDirectoryLoading] = useState(false);
  const [directoryExporting, setDirectoryExporting] = useState(false);
  const [directoryNotice, setDirectoryNotice] = useState<string | null>(null);
  const [assignmentImport, setAssignmentImport] = useState<AssignmentImportPreview | null>(null);
  const [assignmentCsv, setAssignmentCsv] = useState<string | null>(null);
  const [importingAssignments, setImportingAssignments] = useState(false);
  const assignmentFile = useRef<HTMLInputElement>(null);
  useEffect(() => {
    api
      .get('/api/access/matrix')
      .then((r) => setM(r.data))
      .catch(() => setError('Could not load the access map.'));
  }, []);
  useEffect(() => {
    if (me?.role !== 'admin') return;
    Promise.all([api.get('/api/stores'), api.get('/api/foundation/location-traits')])
      .then(([storeResponse, traitResponse]) => {
        setStores(storeResponse.data);
        setLocationTraits(traitResponse.data.items);
      })
      .catch((e: any) =>
        setAssignmentError(e?.response?.data?.detail ?? 'Could not load store assignments.')
      );
  }, [me?.role]);

  useEffect(() => {
    if (me?.role !== 'admin') return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setAssignmentLoading(true);
      try {
        const { data } = await api.get('/api/access/store-assignments', {
          params: {
            q: assignmentQuery.trim() || undefined,
            page: assignmentPage,
            page_size: 50,
          },
        });
        if (cancelled) return;
        setAssignments(data.items);
        setAssignmentTotal(data.total);
        setAssignmentPages(data.pages);
      } catch (e: any) {
        if (!cancelled) {
          setAssignmentError(e?.response?.data?.detail ?? 'Could not load store assignments.');
        }
      } finally {
        if (!cancelled) setAssignmentLoading(false);
      }
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [me?.role, assignmentQuery, assignmentPage, assignmentRefresh]);

  useEffect(() => {
    if (me?.role !== 'admin') return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setDirectoryLoading(true);
      try {
        const { data } = await api.get('/api/access/verified-store-users', {
          params: {
            q: directoryQuery.trim() || undefined,
            role: directoryRole,
            scope_status: directoryScope,
            page: directoryPage,
            page_size: 50,
          },
        });
        if (cancelled) return;
        setVerifiedUsers(data.items);
        setDirectoryTotal(data.total);
        setDirectoryPages(data.pages);
      } catch (e: any) {
        if (!cancelled) {
          setAssignmentError(
            e?.response?.data?.detail ?? 'Could not search the employee directory.'
          );
        }
      } finally {
        if (!cancelled) setDirectoryLoading(false);
      }
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [me?.role, directoryQuery, directoryRole, directoryScope, directoryPage, assignmentRefresh]);

  const editAssignment = (assignment: Assignment) => {
    setUserIdentifier(assignment.user_identifier);
    setSelectedStores(assignment.store_ids);
    setSelectedTraits(assignment.location_traits);
    setEditing(true);
    setAssignmentError(null);
    setAssignmentNotice(null);
  };
  const newAssignment = () => {
    setUserIdentifier('');
    setSelectedStores([]);
    setSelectedTraits([]);
    setEditing(false);
    setAssignmentError(null);
    setAssignmentNotice(null);
  };
  const saveAssignment = async () => {
    if (!userIdentifier.trim()) {
      setAssignmentError('Enter the verified user identifier, usually their email address.');
      return;
    }
    setSaving(true);
    setAssignmentError(null);
    setAssignmentNotice(null);
    try {
      const { data } = await api.put('/api/access/store-assignments', {
        user_identifier: userIdentifier,
        store_ids: selectedStores,
        location_traits: selectedTraits,
      });
      setAssignmentRefresh((current) => current + 1);
      setDirectoryNotice('Employee scope updated. Refreshing directory results.');
      newAssignment();
      setAssignmentNotice(`Store scope saved for ${data.user_identifier}.`);
    } catch (e: any) {
      setAssignmentError(e?.response?.data?.detail ?? 'Could not save this store assignment.');
    } finally {
      setSaving(false);
    }
  };
  if (error) return <Alert tone="error">{error}</Alert>;
  if (!m) return <SkeletonRows rows={8} label="Loading access map" />;
  const groups = Array.from(new Set(m.screens.map((s) => s.group)));
  const visibleAssignments = assignments ?? [];
  const eligibleVerifiedUsers = verifiedUsers.filter((user) =>
    ['store_manager', 'store_staff'].includes(user.role)
  );
  const visibleVerifiedUsers = eligibleVerifiedUsers;
  const assignmentByIdentifier = new Map(
    (assignments ?? []).map((assignment) => [assignment.user_identifier, assignment])
  );
  const employeeAssignment = (user: VerifiedStoreUser) =>
    assignmentByIdentifier.get(user.assignment_identifier) ??
    assignmentByIdentifier.get(user.user_identifier.trim().toLowerCase());
  const selectVerifiedUser = async (user: VerifiedStoreUser) => {
    let current = employeeAssignment(user);
    if (user.has_local_scope && !current) {
      try {
        const { data } = await api.get('/api/access/store-assignments', {
          params: { q: user.assignment_identifier, page: 1, page_size: 50 },
        });
        current = data.items.find(
          (assignment: Assignment) => assignment.user_identifier === user.assignment_identifier
        );
        if (
          !current &&
          user.user_identifier.toLowerCase() !== user.assignment_identifier.toLowerCase()
        ) {
          const { data: legacyData } = await api.get('/api/access/store-assignments', {
            params: { q: user.user_identifier, page: 1, page_size: 50 },
          });
          current = legacyData.items.find(
            (assignment: Assignment) =>
              assignment.user_identifier.toLowerCase() === user.user_identifier.toLowerCase()
          );
        }
      } catch (e: any) {
        setAssignmentError(e?.response?.data?.detail ?? 'Could not load this employee assignment.');
        return;
      }
    }
    if (current) {
      editAssignment(current);
      return;
    }
    setUserIdentifier(user.assignment_identifier);
    setSelectedStores([]);
    setSelectedTraits([]);
    setEditing(false);
    setAssignmentError(null);
    setAssignmentNotice(null);
  };
  const downloadAssignments = async () => {
    setAssignmentExporting(true);
    try {
      const params = new URLSearchParams({ format: 'csv' });
      if (assignmentQuery.trim()) params.set('q', assignmentQuery.trim());
      const { data, headers } = await api.get(
        `/api/access/store-assignments?${params.toString()}`,
        { responseType: 'blob' }
      );
      downloadBlob('store-assignments.csv', data);
      if (headers?.['x-store-assignments-truncated'] === 'true') {
        setAssignmentError(
          'The assignment export reached 50,000 rows. Narrow the search to export the remaining assignments.'
        );
      }
    } catch (e: any) {
      setAssignmentError(e?.response?.data?.detail ?? 'Could not export store assignments.');
    } finally {
      setAssignmentExporting(false);
    }
  };
  const downloadVerifiedUsers = async () => {
    setDirectoryExporting(true);
    setDirectoryNotice(null);
    try {
      const params = new URLSearchParams({ format: 'csv' });
      if (directoryQuery.trim()) params.set('q', directoryQuery.trim());
      if (directoryRole !== 'all') params.set('role', directoryRole);
      if (directoryScope !== 'all') params.set('scope_status', directoryScope);
      const { data, headers } = await api.get(
        `/api/access/verified-store-users?${params.toString()}`,
        { responseType: 'blob' }
      );
      downloadBlob('verified-store-employees.csv', data);
      if (headers?.['x-verified-users-truncated'] === 'true') {
        setDirectoryNotice(
          'The employee export reached 50,000 rows. Narrow the role or scope filters to export the remaining employees.'
        );
      }
    } catch (e: any) {
      setAssignmentError(e?.response?.data?.detail ?? 'Could not export the employee directory.');
    } finally {
      setDirectoryExporting(false);
    }
  };
  const downloadAssignmentTemplate = () =>
    downloadCsv(
      'store-assignment-template.csv',
      ['assignment_identifier', 'store_ids', 'location_traits'],
      []
    );
  const previewAssignmentImport = async (file?: File) => {
    if (!file) return;
    setAssignmentError(null);
    setAssignmentNotice(null);
    setImportingAssignments(true);
    try {
      const csvText = await file.text();
      const { data } = await api.post<AssignmentImportPreview>(
        '/api/access/store-assignments/import/preview',
        { csv_text: csvText }
      );
      setAssignmentCsv(csvText);
      setAssignmentImport(data);
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      setAssignmentError(
        typeof detail === 'string'
          ? detail
          : (detail?.errors
              ?.map((item: { row: number; message: string }) => `Row ${item.row}: ${item.message}`)
              .join(' · ') ?? 'Could not preview store assignment CSV.')
      );
    } finally {
      setImportingAssignments(false);
      if (assignmentFile.current) assignmentFile.current.value = '';
    }
  };
  const applyAssignmentImport = async () => {
    if (!assignmentImport?.valid || !assignmentImport.preview_token || assignmentCsv === null)
      return;
    setImportingAssignments(true);
    setAssignmentError(null);
    setAssignmentNotice(null);
    try {
      const { data } = await api.post('/api/access/store-assignments/import', {
        csv_text: assignmentCsv,
        preview_token: assignmentImport.preview_token,
      });
      setAssignmentRefresh((current) => current + 1);
      setAssignmentImport(null);
      setAssignmentCsv(null);
      setAssignmentNotice(
        `Import applied: ${data.applied.created} created, ${data.applied.replaced} replaced, ${data.applied.unchanged} unchanged.`
      );
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      setAssignmentError(
        typeof detail === 'string'
          ? detail
          : (detail?.errors
              ?.map((item: { row: number; message: string }) => `Row ${item.row}: ${item.message}`)
              .join(' · ') ?? 'Could not apply this assignment import.')
      );
      if (e?.response?.status === 409) {
        setAssignmentImport(null);
        setAssignmentCsv(null);
      }
    } finally {
      setImportingAssignments(false);
    }
  };
  return (
    <>
      <SettingsSection
        title="Roles & access"
        description="Who can open each screen. The API enforces the same map, so a role without a screen cannot call its data either. Administrators can open everything and can act as another role from the user menu. Your role is highlighted."
      >
        <Alert tone="info">
          <b>Store managers and store staff are limited to assigned stores.</b> Admin-managed
          explicit stores and location traits take precedence over the store list in Ontology
          Studio. Trait assignments follow active Foundation stores as traits change. Without a
          local assignment, the verified identity's store claim applies. An empty assignment grants
          no store access.
        </Alert>
        <Card flush>
          <div className="table-scroll">
            <table className="data-table access-matrix">
              <thead>
                <tr>
                  <th>Screen</th>
                  {m.roles.map((r) => (
                    <th key={r.key} className={`num ${r.key === m.you ? 'you' : ''}`}>
                      {r.label}
                      {r.key === m.you && (
                        <small className="act-sub" style={{ display: 'block' }}>
                          You
                        </small>
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {groups.map((g) => (
                  <Fragment key={g}>
                    <tr className="grp">
                      <td colSpan={m.roles.length + 1}>{g}</td>
                    </tr>
                    {m.screens
                      .filter((s) => s.group === g)
                      .map((s) => (
                        <tr key={s.key}>
                          <td className="font-medium">
                            {s.label}
                            <small className="act-sub" style={{ display: 'block' }}>
                              {s.path}
                            </small>
                          </td>
                          {m.roles.map((r) => {
                            const ok = s.roles.includes(r.key);
                            return (
                              <td
                                key={r.key}
                                className={`num ${r.key === m.you ? 'you' : ''}`}
                                aria-label={`${r.label}: ${ok ? 'can open' : 'cannot open'} ${s.label}`}
                              >
                                {ok ? <span className="ok">●</span> : <span className="no">–</span>}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </SettingsSection>
      {me?.role === 'admin' && (
        <SettingsSection
          title="Store assignments"
          description="Assign verified store managers and store staff to the locations they may access. Assignments are audited and take effect on their next request."
        >
          {assignmentError && <Alert tone="error">{assignmentError}</Alert>}
          {assignmentNotice && <Alert tone="success">{assignmentNotice}</Alert>}
          <Card flush title="Verified store employees">
            <p className="hint">
              Identity details come from Ontology Studio sign-ins. Use Manage scope to review or
              create a local store assignment. Search and filters apply to the full verified
              directory, not only the employees on this page.
            </p>
            <div className="toolbar">
              <input
                className="grow"
                type="search"
                aria-label="Search verified store employees"
                placeholder="Search name, email, identity ID, or role"
                value={directoryQuery}
                onChange={(event) => {
                  setDirectoryQuery(event.target.value);
                  setDirectoryPage(1);
                }}
              />
              <Field label="Role">
                <select
                  value={directoryRole}
                  onChange={(event) => {
                    setDirectoryRole(event.target.value as typeof directoryRole);
                    setDirectoryPage(1);
                  }}
                >
                  <option value="all">All store roles</option>
                  <option value="store_manager">Store managers</option>
                  <option value="store_staff">Store staff</option>
                </select>
              </Field>
              <Field label="Local scope">
                <select
                  value={directoryScope}
                  onChange={(event) => {
                    setDirectoryScope(event.target.value as typeof directoryScope);
                    setDirectoryPage(1);
                  }}
                >
                  <option value="all">All employees</option>
                  <option value="assigned">Local assignment</option>
                  <option value="unassigned">Identity claims only</option>
                </select>
              </Field>
              <Button
                size="sm"
                variant="secondary"
                disabled={!directoryTotal || directoryExporting}
                onClick={() => void downloadVerifiedUsers()}
              >
                {directoryExporting ? 'Preparing CSV…' : 'Download matching employees'}
              </Button>
              <span className="toolbar-count">
                {directoryLoading
                  ? 'Searching employees…'
                  : `${directoryTotal} matching employee${directoryTotal === 1 ? '' : 's'}`}
              </span>
            </div>
            {directoryNotice && (
              <Alert tone="warning" onDismiss={() => setDirectoryNotice(null)}>
                {directoryNotice}
              </Alert>
            )}
            {assignments === null || directoryLoading ? (
              <SkeletonRows rows={3} label="Loading verified store employees" />
            ) : visibleVerifiedUsers.length === 0 ? (
              <p className="hint">
                {directoryTotal === 0
                  ? 'No verified employees match these filters.'
                  : 'No verified store employees match this search.'}
              </p>
            ) : (
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Employee</th>
                      <th>Role</th>
                      <th>Stable identity ID</th>
                      <th>Last seen</th>
                      <th>Local scope</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {visibleVerifiedUsers.map((employee) => {
                      const assignment = employeeAssignment(employee);
                      return (
                        <tr key={employee.assignment_identifier}>
                          <td>
                            <b>{employee.display_name}</b>
                            <small className="act-sub" style={{ display: 'block' }}>
                              {employee.user_identifier}
                            </small>
                          </td>
                          <td>
                            {employee.role === 'store_manager' ? 'Store manager' : 'Store staff'}
                          </td>
                          <td>
                            <code>{employee.identity_id ?? employee.assignment_identifier}</code>
                          </td>
                          <td>{formatDateTime(employee.last_seen_at)}</td>
                          <td>
                            {assignment
                              ? `${assignment.store_ids.length} stores · ${assignment.location_traits.length} traits`
                              : employee.has_local_scope
                                ? 'Local assignment'
                                : 'Identity claims'}
                          </td>
                          <td>
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => void selectVerifiedUser(employee)}
                            >
                              {assignment ? 'Manage scope' : 'Assign stores'}
                            </Button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <div className="row-actions" style={{ marginTop: '1rem' }}>
              <Button
                size="sm"
                variant="secondary"
                disabled={directoryLoading || directoryPage <= 1}
                onClick={() => setDirectoryPage((current) => Math.max(1, current - 1))}
              >
                Previous
              </Button>
              <span className="hint">
                {directoryTotal === 0
                  ? 'No matching employees'
                  : `Page ${directoryPage} of ${directoryPages} · showing ${(directoryPage - 1) * 50 + 1}–${Math.min(directoryPage * 50, directoryTotal)} of ${directoryTotal}`}
              </span>
              <Button
                size="sm"
                variant="secondary"
                disabled={directoryLoading || directoryPage >= directoryPages}
                onClick={() => setDirectoryPage((current) => current + 1)}
              >
                Next
              </Button>
            </div>
          </Card>
          <Card title={editing ? 'Edit store scope' : 'Assign stores'}>
            <div className="cfg-form">
              <Field
                label="Verified assignment identifier"
                hint="Choose a verified store manager or store staff identity. New users appear after their first sign-in."
              >
                <input
                  value={userIdentifier}
                  onChange={(event) => setUserIdentifier(event.target.value)}
                  maxLength={255}
                  autoComplete="off"
                  list="verified-store-users"
                />
                <datalist id="verified-store-users">
                  {eligibleVerifiedUsers.map((verifiedUser) => (
                    <option
                      key={verifiedUser.user_identifier}
                      value={verifiedUser.assignment_identifier}
                      label={`${verifiedUser.display_name} · ${verifiedUser.user_identifier} · ${verifiedUser.role}`}
                    />
                  ))}
                </datalist>
              </Field>
              <Field
                label="Explicit stores"
                hint="Combined with location traits below. Leave both selections empty to explicitly deny store access."
              >
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
                    gap: '.5rem',
                    maxHeight: '15rem',
                    overflowY: 'auto',
                    padding: '.5rem',
                    border: '1px solid var(--line)',
                    borderRadius: 'var(--radius-sm)',
                  }}
                >
                  {stores.map((store) => (
                    <label key={store} className="check-row">
                      <input
                        type="checkbox"
                        checked={selectedStores.includes(store)}
                        onChange={(event) =>
                          setSelectedStores((current) =>
                            event.target.checked
                              ? [...current, store]
                              : current.filter((value) => value !== store)
                          )
                        }
                      />
                      {store}
                    </label>
                  ))}
                  {stores.length === 0 && <span className="hint">No stores are available.</span>}
                </div>
              </Field>
              <Field
                label="Location traits"
                hint="Every active store carrying a selected trait is included automatically when Foundation data changes."
              >
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                    gap: '.5rem',
                    maxHeight: '12rem',
                    overflowY: 'auto',
                    padding: '.5rem',
                    border: '1px solid var(--line)',
                    borderRadius: 'var(--radius-sm)',
                  }}
                >
                  {locationTraits.map((trait) => (
                    <label key={trait} className="check-row">
                      <input
                        type="checkbox"
                        checked={selectedTraits.includes(trait)}
                        onChange={(event) =>
                          setSelectedTraits((current) =>
                            event.target.checked
                              ? [...current, trait]
                              : current.filter((value) => value !== trait)
                          )
                        }
                      />
                      {trait}
                    </label>
                  ))}
                  {locationTraits.length === 0 && (
                    <span className="hint">
                      Add location traits to active Store records in Foundation Data first.
                    </span>
                  )}
                </div>
              </Field>
            </div>
            <div className="row-actions" style={{ marginTop: '1rem' }}>
              <Button disabled={saving || !userIdentifier.trim()} onClick={saveAssignment}>
                {saving ? 'Saving…' : editing ? 'Save assignment' : 'Assign stores'}
              </Button>
              {editing && (
                <Button variant="secondary" disabled={saving} onClick={newAssignment}>
                  New assignment
                </Button>
              )}
            </div>
          </Card>
          <Card flush title="Current assignments">
            {assignments === null || assignmentLoading ? (
              <SkeletonRows rows={3} label="Loading store assignments" />
            ) : (
              <>
                <div className="toolbar">
                  <input
                    className="grow"
                    type="search"
                    aria-label="Search store assignments"
                    placeholder="Search assignment identifier"
                    value={assignmentQuery}
                    onChange={(event) => {
                      setAssignmentQuery(event.target.value);
                      setAssignmentPage(1);
                    }}
                  />
                  <span className="toolbar-count">
                    {assignmentTotal} matching assignment{assignmentTotal === 1 ? '' : 's'}
                  </span>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={!assignmentTotal || assignmentExporting}
                    onClick={() => void downloadAssignments()}
                  >
                    {assignmentExporting ? 'Preparing CSV…' : 'Download matching CSV'}
                  </Button>
                </div>
                {assignmentTotal === 0 ? (
                  <p className="hint">
                    {assignmentQuery.trim()
                      ? 'No assignments match this search.'
                      : 'No local assignments. Store scope currently comes from identity claims.'}
                  </p>
                ) : visibleAssignments.length === 0 ? (
                  <p className="hint">No assignments match this search.</p>
                ) : (
                  <div className="table-scroll">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>User identifier</th>
                          <th>Explicit stores</th>
                          <th>Location traits</th>
                          <th>Updated</th>
                          <th>Updated by</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {visibleAssignments.map((assignment) => (
                          <tr key={assignment.id}>
                            <td>{assignment.user_identifier}</td>
                            <td>
                              {assignment.store_ids.length
                                ? assignment.store_ids.join(', ')
                                : 'No access'}
                            </td>
                            <td>
                              {assignment.location_traits.length
                                ? assignment.location_traits.join(', ')
                                : 'None'}
                            </td>
                            <td>{formatDateTime(assignment.updated_at)}</td>
                            <td>{assignment.updated_by}</td>
                            <td>
                              <Button
                                size="sm"
                                variant="secondary"
                                onClick={() => editAssignment(assignment)}
                              >
                                Edit
                              </Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {assignmentTotal > 0 && (
                  <div className="row-actions" style={{ marginTop: '.75rem' }}>
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={assignmentLoading || assignmentPage <= 1}
                      onClick={() => setAssignmentPage((current) => Math.max(1, current - 1))}
                    >
                      Previous
                    </Button>
                    <span className="hint">
                      Page {assignmentPage} of {assignmentPages} · showing{' '}
                      {(assignmentPage - 1) * 50 + 1}–
                      {Math.min(assignmentPage * 50, assignmentTotal)} of {assignmentTotal}
                    </span>
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={assignmentLoading || assignmentPage >= assignmentPages}
                      onClick={() => setAssignmentPage((current) => current + 1)}
                    >
                      Next
                    </Button>
                  </div>
                )}
              </>
            )}
            <div className="form-row">
              <Button variant="secondary" onClick={downloadAssignmentTemplate}>
                Download CSV template
              </Button>
              <input
                ref={assignmentFile}
                type="file"
                accept=".csv,text/csv"
                hidden
                onChange={(event) => void previewAssignmentImport(event.target.files?.[0])}
              />
              <Button
                variant="secondary"
                disabled={importingAssignments}
                onClick={() => assignmentFile.current?.click()}
              >
                {importingAssignments ? 'Preparing import…' : 'Import assignments CSV'}
              </Button>
              <span className="hint">
                Use the verified assignment identifier and semicolons between store IDs and location
                traits. Empty both scope columns to explicitly deny access. Imports are limited to
                5,000 rows and apply only after every row validates.
              </span>
            </div>
            {assignmentImport && (
              <div className="stack stack-sm">
                <p>
                  <b>Import preview:</b> {assignmentImport.counts.Create} create ·{' '}
                  {assignmentImport.counts.Replace} replace · {assignmentImport.counts.Unchanged}{' '}
                  unchanged
                </p>
                {assignmentImport.errors.length > 0 && (
                  <Alert tone="error">
                    {assignmentImport.errors
                      .slice(0, 10)
                      .map((item) => `Row ${item.row}: ${item.message}`)
                      .join(' · ')}
                    {assignmentImport.errors_truncated && ' · Additional errors omitted.'}
                  </Alert>
                )}
                {assignmentImport.valid && (
                  <>
                    <p className="hint">
                      Previewing {assignmentImport.rows.length} assignment rows. First rows are
                      shown below; the complete file is validated before it can be applied.
                    </p>
                    <div className="table-scroll">
                      <table className="data-table">
                        <thead>
                          <tr>
                            <th>CSV row</th>
                            <th>Assignment identifier</th>
                            <th>Change</th>
                            <th>Stores</th>
                            <th>Traits</th>
                          </tr>
                        </thead>
                        <tbody>
                          {assignmentImport.rows.slice(0, 10).map((row) => (
                            <tr key={`${row.row}-${row.user_identifier}`}>
                              <td>{row.row}</td>
                              <td>{row.user_identifier}</td>
                              <td>{row.action}</td>
                              <td>{row.store_ids.join(', ') || 'No access'}</td>
                              <td>{row.location_traits.join(', ') || 'None'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
                <div className="row-actions">
                  <Button
                    variant="primary"
                    disabled={!assignmentImport.valid || importingAssignments}
                    onClick={() => void applyAssignmentImport()}
                  >
                    {importingAssignments ? 'Applying…' : 'Apply all assignments'}
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={importingAssignments}
                    onClick={() => {
                      setAssignmentImport(null);
                      setAssignmentCsv(null);
                    }}
                  >
                    Discard preview
                  </Button>
                </div>
              </div>
            )}
          </Card>
        </SettingsSection>
      )}
    </>
  );
}
