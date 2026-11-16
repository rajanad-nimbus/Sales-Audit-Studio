import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { RoleAssignments } from '@/components/settings/RoleAssignments';
import { ME, calls, renderAs } from '@/test/utils';

const ROLES = [
  { key: 'finance', label: 'Finance' },
  { key: 'it', label: 'IT' },
  { key: 'store_manager', label: 'Store manager' },
];

const person = (over: Record<string, unknown> = {}) => ({
  assignment_identifier: 'u-7',
  user_identifier: 'pat@example.test',
  display_name: 'Pat Doe',
  last_seen_at: '2026-10-01T10:00:00Z',
  signed_in_role: 'viewer',
  assigned_role: null,
  assigned_by: null,
  assigned_at: null,
  reason: null,
  ...over,
});

const listing = (items: unknown[]) => ({
  roles: ROLES,
  items,
  pagination: { page: 1, page_size: 25, total: items.length, pages: 1 },
});

describe('RoleAssignments', () => {
  it('lists people who have signed in with the role Studio gave and the one assigned here', async () => {
    await renderAs(<RoleAssignments />, ME('admin'), {
      'GET /api/access/role-assignments': listing([
        person(),
        person({
          assignment_identifier: 'u-8',
          user_identifier: 'sam@example.test',
          display_name: 'Sam Roe',
          assigned_role: 'finance',
          assigned_by: 'admin@example.test',
          reason: 'Finance desk cover',
        }),
      ]),
    });

    const pat = (await screen.findByText('Pat Doe')).closest('tr') as HTMLElement;
    expect(within(pat).getByRole('button', { name: 'Assign…' })).toBeInTheDocument();
    const sam = screen.getByText('Sam Roe').closest('tr') as HTMLElement;
    expect(within(sam).getByText('Finance')).toBeInTheDocument();
    expect(within(sam).getByRole('button', { name: 'Change…' })).toBeInTheDocument();
  });

  it('saves a role only with a reason of at least ten characters', async () => {
    await renderAs(<RoleAssignments />, ME('admin'), {
      'GET /api/access/role-assignments': listing([person()]),
      'PUT /api/access/role-assignments': person({ assigned_role: 'finance' }),
    });
    await userEvent.click(await screen.findByRole('button', { name: 'Assign…' }));
    const save = screen.getByRole('button', { name: 'Save role' });
    expect(save).toBeDisabled();

    await userEvent.selectOptions(screen.getByLabelText('Role'), 'finance');
    await userEvent.type(screen.getByLabelText(/Reason/), 'too short');
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Reason/), ' now long enough');
    await userEvent.click(save);

    await waitFor(() => expect(calls('put', '/api/access/role-assignments')).toHaveLength(1));
    expect(calls('put', '/api/access/role-assignments')[0][1]).toEqual({
      assignment_identifier: 'u-7',
      role: 'finance',
      reason: 'too short now long enough',
    });
    expect(await screen.findByText('Pat Doe now holds the Finance role.')).toBeInTheDocument();
  });

  it('removes an assigned role with a reason, handing the Studio role back', async () => {
    await renderAs(<RoleAssignments />, ME('admin'), {
      'GET /api/access/role-assignments': listing([person({ assigned_role: 'it' })]),
      'DELETE /api/access/role-assignments/u-7': { assignment_identifier: 'u-7', removed: true },
    });
    await userEvent.click(await screen.findByRole('button', { name: 'Change…' }));
    await userEvent.type(screen.getByLabelText(/Reason/), 'Moved to another team');
    await userEvent.click(screen.getByRole('button', { name: 'Remove assigned role' }));

    await waitFor(() =>
      expect(calls('delete', '/api/access/role-assignments/u-7')).toHaveLength(1)
    );
    expect(calls('delete', '/api/access/role-assignments/u-7')[0][1]).toEqual({
      params: { reason: 'Moved to another team' },
    });
    expect(
      await screen.findByText('Pat Doe goes back to their Ontology Studio role.')
    ).toBeInTheDocument();
  });

  it('says how people get onto the list when it is empty', async () => {
    await renderAs(<RoleAssignments />, ME('admin'), {
      'GET /api/access/role-assignments': listing([]),
    });
    expect(
      await screen.findByText(/People appear here after their first sign-in/)
    ).toBeInTheDocument();
  });
});
