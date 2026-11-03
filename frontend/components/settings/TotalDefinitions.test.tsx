import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { TotalDefinitions } from '@/components/settings/TotalDefinitions';
import type { TotalRevision } from '@/components/settings/TotalDefinitions';
import { ME, apiFailure, calls, renderAs } from '@/test/utils';

const revision = (over: Partial<TotalRevision>): TotalRevision => ({
  id: 'live',
  name: 'Sales',
  revision: 1,
  status: 'Active',
  description: null,
  aggregation: 'Sum',
  measure: 'Amount',
  source_system: 'POS',
  transaction_types: [],
  tender_type: null,
  level: 'Store',
  active: true,
  start_date: null,
  end_date: null,
  calculation_basis: 'Raw',
  combined_total_ids: [],
  combined_operator: null,
  updated_by: 'editor',
  ...over,
});

const LIVE = revision({});
const DRAFT = revision({ id: 'draft', revision: 2, status: 'Worksheet', aggregation: 'Count' });
const PENDING = revision({
  id: 'draft',
  revision: 2,
  status: 'Pending Approval',
  aggregation: 'Count',
  submitted_by: 'editor',
  submitted_at: '2026-10-02T09:00:00Z',
  submitted_impact_fingerprint: 'a'.repeat(64),
});

const impact = (over: Record<string, unknown> = {}) => ({
  definition_id: 'draft',
  revision: 2,
  replaces_revision: 1,
  date_range: { start: null, end: null },
  limit: 500,
  truncated: false,
  fingerprint: 'a'.repeat(64),
  summary: {
    store_days: 2,
    changed: 1,
    net_change: '-13',
    absolute_change: '13',
    closed: 0,
    would_go_stale: 1,
    closure_requests_superseded: 0,
    errors: 0,
    rules_newly_breached: 0,
    rules_cleared: 0,
  },
  rows: [],
  ...over,
});

async function openTotal(revisions: TotalRevision[], me = ME('finance', 'editor'), routes = {}) {
  const onChanged = vi.fn().mockResolvedValue(undefined);
  await renderAs(<TotalDefinitions revisions={revisions} onChanged={onChanged} />, me, routes);
  await userEvent.click(await screen.findByText('Sales'));
  return { onChanged, drawer: await screen.findByRole('dialog', { name: 'Sales' }) };
}

describe('TotalDefinitions: lifecycle', () => {
  it('shows a draft, a submission and a live revision distinctly in the list', async () => {
    await renderAs(
      <TotalDefinitions
        revisions={[
          LIVE,
          DRAFT,
          revision({ id: 'b', name: 'Tax', status: 'Pending Approval', revision: 3 }),
        ]}
        onChanged={vi.fn()}
      />
    );
    expect(await screen.findByText('Live r1')).toBeInTheDocument();
    expect(screen.getByText('Draft r2')).toBeInTheDocument();
    expect(screen.getByText('Awaiting approval r3')).toBeInTheDocument();
    expect(screen.getByText(/1 draft waiting to be previewed and submitted/)).toBeInTheDocument();
    expect(screen.getByText(/1 awaiting a second approver/)).toBeInTheDocument();
  });
});

describe('TotalDefinitions: submitting a draft', () => {
  it('cannot be submitted until its impact has been previewed', async () => {
    const { drawer } = await openTotal([LIVE, DRAFT]);
    expect(within(drawer).getByRole('button', { name: 'Submit r2 for approval' })).toBeDisabled();
    expect(within(drawer).queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument();
  });

  it('submits with the fingerprint of the preview that was reviewed', async () => {
    const { drawer, onChanged } = await openTotal([LIVE, DRAFT], ME('finance', 'editor'), {
      'POST /api/total-definitions/draft/impact': impact(),
      'POST /api/total-definitions/draft/submit': {},
    });

    await userEvent.click(within(drawer).getByRole('button', { name: 'Preview impact' }));
    expect(await within(drawer).findByText('1 of 2')).toBeInTheDocument();
    await userEvent.click(within(drawer).getByRole('button', { name: 'Submit r2 for approval' }));

    await waitFor(() =>
      expect(calls('post', '/api/total-definitions/draft/submit')).toHaveLength(1)
    );
    expect(calls('post', '/api/total-definitions/draft/submit')[0][1]).toEqual({
      impact_fingerprint: 'a'.repeat(64),
    });
    expect(onChanged).toHaveBeenCalled();
  });

  it('will not submit a draft whose preview would fail to calculate', async () => {
    const bad = impact({ summary: { ...impact().summary, errors: 2 } });
    const { drawer } = await openTotal([LIVE, DRAFT], ME('finance', 'editor'), {
      'POST /api/total-definitions/draft/impact': bad,
    });
    await userEvent.click(within(drawer).getByRole('button', { name: 'Preview impact' }));

    expect(
      await within(drawer).findByText(/2 store days would fail to calculate/)
    ).toBeInTheDocument();
    expect(within(drawer).getByRole('button', { name: 'Submit r2 for approval' })).toBeDisabled();
  });

  it('warns when audit rules would newly fail', async () => {
    const risky = impact({ summary: { ...impact().summary, rules_newly_breached: 3 } });
    const { drawer } = await openTotal([LIVE, DRAFT], ME('finance', 'editor'), {
      'POST /api/total-definitions/draft/impact': risky,
    });
    await userEvent.click(within(drawer).getByRole('button', { name: 'Preview impact' }));
    expect(
      await within(drawer).findByText(/3 audit rule results would newly fail/)
    ).toBeInTheDocument();
  });

  it('discards the preview and shows the reason when the impact moved', async () => {
    const { drawer } = await openTotal([LIVE, DRAFT], ME('finance', 'editor'), {
      'POST /api/total-definitions/draft/impact': impact(),
      'POST /api/total-definitions/draft/submit': apiFailure(
        'The impact has changed since it was reviewed; preview it again',
        409
      ),
    });
    await userEvent.click(within(drawer).getByRole('button', { name: 'Preview impact' }));
    await userEvent.click(
      await within(drawer).findByRole('button', { name: 'Submit r2 for approval' })
    );

    expect(await within(drawer).findByText(/preview it again/)).toBeInTheDocument();
    expect(within(drawer).getByRole('button', { name: 'Submit r2 for approval' })).toBeDisabled();
  });
});

describe('TotalDefinitions: a second approver', () => {
  it('gives the editor no way to approve their own submission', async () => {
    const { drawer } = await openTotal([LIVE, PENDING], ME('finance', 'editor'));

    expect(
      within(drawer).getByText(/Another Finance user must review the impact/)
    ).toBeInTheDocument();
    expect(within(drawer).queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument();
    expect(within(drawer).queryByRole('button', { name: 'Return' })).not.toBeInTheDocument();
  });

  it('keeps the submitter out even when someone else edited it last', async () => {
    const edited = { ...PENDING, updated_by: 'someone-else', submitted_by: 'submitter' };
    const { drawer } = await openTotal([LIVE, edited], ME('finance', 'submitter'));
    expect(within(drawer).queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument();
  });

  it('lets a different user approve once the impact matches what was submitted', async () => {
    const { drawer, onChanged } = await openTotal([LIVE, PENDING], ME('finance', 'approver'), {
      'POST /api/total-definitions/draft/impact': impact(),
      'POST /api/total-definitions/draft/activate': {},
    });
    const approve = within(drawer).getByRole('button', { name: 'Approve & activate r2' });
    expect(approve).toBeDisabled();

    await userEvent.click(within(drawer).getByRole('button', { name: 'Preview impact' }));
    await waitFor(() => expect(approve).toBeEnabled());
    await userEvent.click(approve);

    await waitFor(() =>
      expect(calls('post', '/api/total-definitions/draft/activate')).toHaveLength(1)
    );
    expect(calls('post', '/api/total-definitions/draft/activate')[0][1]).toEqual({
      impact_fingerprint: 'a'.repeat(64),
    });
    expect(onChanged).toHaveBeenCalled();
  });

  it('blocks approval when the impact differs from the one submitted', async () => {
    const { drawer } = await openTotal([LIVE, PENDING], ME('finance', 'approver'), {
      'POST /api/total-definitions/draft/impact': impact({ fingerprint: 'b'.repeat(64) }),
    });
    await userEvent.click(within(drawer).getByRole('button', { name: 'Preview impact' }));

    expect(
      await within(drawer).findByText(/The impact has changed since this was submitted/)
    ).toBeInTheDocument();
    expect(within(drawer).getByRole('button', { name: 'Approve & activate r2' })).toBeDisabled();
  });

  it('returns a submission to draft only with a reason', async () => {
    const { drawer, onChanged } = await openTotal([LIVE, PENDING], ME('finance', 'approver'), {
      'POST /api/total-definitions/draft/return': {},
    });
    const send = within(drawer).getByRole('button', { name: 'Return' });
    expect(send).toBeDisabled();

    await userEvent.type(within(drawer).getByLabelText('Return to draft'), 'Count is wrong');
    await userEvent.click(send);
    await waitFor(() =>
      expect(calls('post', '/api/total-definitions/draft/return')).toHaveLength(1)
    );
    expect(calls('post', '/api/total-definitions/draft/return')[0][1]).toEqual({
      rationale: 'Count is wrong',
    });
    expect(onChanged).toHaveBeenCalled();
  });
});
