import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { CloseControls } from '@/components/settings/CloseControls';
import { ME, apiFailure, calls, renderAs } from '@/test/utils';

const RULE = {
  id: 'r1',
  total_name: 'POS to GL Variance',
  location_trait: 'Outlet',
  within_amount: '1.00',
  case_amount: '10.00',
  updated_by: 'finance',
  updated_at: '2026-10-01T00:00:00Z',
};
const PERIOD = {
  id: 'p1',
  period_end: '2026-09-30',
  status: 'Closed',
  reason: 'September month-end',
  closed_by: 'finance',
  closed_at: '2026-10-02T09:00:00Z',
  reopened_by: null,
  reopened_reason: null,
};
const routes = (extra = {}) => ({
  'GET /api/variance-tolerances': [RULE],
  'GET /api/total-definitions': [{ name: 'Sales' }],
  'GET /api/periods': { closed_through: '2026-09-30', periods: [PERIOD] },
  ...extra,
});

describe('CloseControls: tolerances', () => {
  it('lists each rule with what it covers and its two thresholds', async () => {
    await renderAs(<CloseControls />, ME('auditor'), routes());

    const row = (await screen.findByText('POS to GL Variance')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Outlet')).toBeInTheDocument();
    expect(within(row).getByText('up to $1.00')).toBeInTheDocument();
    expect(within(row).getByText('above $10.00')).toBeInTheDocument();
  });

  it('hides every editing control from a role that cannot change them', async () => {
    await renderAs(<CloseControls />, ME('auditor'), routes());
    await screen.findByText('POS to GL Variance');

    expect(screen.queryByText('Add a tolerance')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Close period' })).not.toBeInTheDocument();
  });

  it('refuses a case threshold below the tolerance before anything is sent', async () => {
    await renderAs(<CloseControls />, ME('finance'), routes());
    await screen.findByText('Add a tolerance');

    await userEvent.clear(screen.getByLabelText('Tolerance'));
    await userEvent.type(screen.getByLabelText('Tolerance'), '5');
    await userEvent.clear(screen.getByLabelText('Case threshold'));
    await userEvent.type(screen.getByLabelText('Case threshold'), '2');

    expect(screen.getByRole('button', { name: 'Add tolerance' })).toBeDisabled();
    expect(
      screen.getByText('The case threshold cannot be below the tolerance.')
    ).toBeInTheDocument();
  });

  it('adds a rule, treating blank scopes as "everything"', async () => {
    await renderAs(
      <CloseControls />,
      ME('finance'),
      routes({ 'POST /api/variance-tolerances': {} })
    );
    await screen.findByText('Add a tolerance');

    await userEvent.clear(screen.getByLabelText('Tolerance'));
    await userEvent.type(screen.getByLabelText('Tolerance'), '2');
    await userEvent.clear(screen.getByLabelText('Case threshold'));
    await userEvent.type(screen.getByLabelText('Case threshold'), '20');
    await userEvent.click(screen.getByRole('button', { name: 'Add tolerance' }));

    await waitFor(() => expect(calls('post', '/api/variance-tolerances')).toHaveLength(1));
    expect(calls('post', '/api/variance-tolerances')[0][1]).toEqual({
      total_name: null,
      location_trait: null,
      within_amount: '2',
      case_amount: '20',
    });
  });

  it('edits a rule by loading it into the form and saving over it', async () => {
    await renderAs(
      <CloseControls />,
      ME('finance'),
      routes({ 'PUT /api/variance-tolerances/r1': {} })
    );
    await userEvent.click(await screen.findByRole('button', { name: 'Edit' }));

    expect(screen.getByLabelText(/^Total/)).toHaveValue('POS to GL Variance');
    await userEvent.click(screen.getByRole('button', { name: 'Save tolerance' }));
    await waitFor(() => expect(calls('put', '/api/variance-tolerances/r1')).toHaveLength(1));
    expect(calls('put', '/api/variance-tolerances/r1')[0][1]).toMatchObject({
      total_name: 'POS to GL Variance',
      location_trait: 'Outlet',
    });
  });

  it('removes a rule and shows a server refusal when adding a duplicate', async () => {
    await renderAs(
      <CloseControls />,
      ME('finance'),
      routes({
        'DELETE /api/variance-tolerances/r1': {},
        'POST /api/variance-tolerances': apiFailure(
          'A tolerance rule for that total and store group exists',
          409
        ),
      })
    );
    await userEvent.click(await screen.findByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(calls('delete', '/api/variance-tolerances/r1')).toHaveLength(1));

    await userEvent.click(screen.getByRole('button', { name: 'Add tolerance' }));
    expect(
      await screen.findByText('A tolerance rule for that total and store group exists')
    ).toBeInTheDocument();
  });
});

describe('CloseControls: period close', () => {
  it('shows how far the books are closed and the history', async () => {
    await renderAs(<CloseControls />, ME('auditor'), routes());

    const row = (await screen.findByText('September month-end')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Closed')).toBeInTheDocument();
    expect(screen.getAllByText('2026-09-30').length).toBeGreaterThan(0);
  });

  it('closes a period through a date with a note', async () => {
    await renderAs(<CloseControls />, ME('finance'), routes({ 'POST /api/periods': {} }));
    await screen.findByText('September month-end');

    await userEvent.type(screen.getByLabelText('Close through'), '2026-10-31');
    await userEvent.type(screen.getByLabelText('Note'), 'October month-end');
    await userEvent.click(screen.getByRole('button', { name: 'Close period' }));

    await waitFor(() => expect(calls('post', '/api/periods')).toHaveLength(1));
    expect(calls('post', '/api/periods')[0][1]).toEqual({
      period_end: '2026-10-31',
      reason: 'October month-end',
    });
  });

  it('needs a reason to reopen the latest period, and shows why a close is refused', async () => {
    await renderAs(
      <CloseControls />,
      ME('finance'),
      routes({
        'POST /api/periods/p1/reopen': {},
        'POST /api/periods': apiFailure(
          'Close every store day in the period first: 2 still open',
          422
        ),
      })
    );
    await screen.findByText('September month-end');

    const reopen = screen.getByRole('button', { name: 'Reopen period' });
    expect(reopen).toBeDisabled();
    await userEvent.type(
      screen.getByLabelText(/Reopen the period through 2026-09-30/),
      'Audit fix'
    );
    await userEvent.click(reopen);
    await waitFor(() => expect(calls('post', '/api/periods/p1/reopen')).toHaveLength(1));
    expect(calls('post', '/api/periods/p1/reopen')[0][1]).toEqual({ rationale: 'Audit fix' });

    await userEvent.type(screen.getByLabelText('Close through'), '2026-10-31');
    await userEvent.click(screen.getByRole('button', { name: 'Close period' }));
    expect(await screen.findByText(/2 still open/)).toBeInTheDocument();
  });
});
