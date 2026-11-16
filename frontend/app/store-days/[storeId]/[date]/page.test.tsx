import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Suspense } from 'react';
import { describe, expect, it } from 'vitest';
import StoreDayDetailPage from '@/app/store-days/[storeId]/[date]/page';
import { ME, calls, renderAs } from '@/test/utils';

const DAY = '/api/store-days/S1/2026-10-01';

const total = (over: Record<string, unknown>) => ({
  id: 't-gl',
  version: 1,
  name: 'POS to GL Variance',
  level: 'Store',
  dimension: null,
  calculated_amount: '100.00',
  declared_amount: '97.00',
  variance_amount: '3.00',
  currency: 'USD',
  outcome: 'Needs reason',
  resolved: false,
  tolerance: {
    within_amount: '1.00',
    case_amount: '10.00',
    rule_id: 'r',
    total_name: null,
    location_trait: null,
  },
  explanation: null,
  data_quality: 'Final',
  ...over,
});

const detail = (over: Record<string, unknown> = {}) => ({
  store_day: {
    id: 'd',
    store_id: 'S1',
    business_date: '2026-10-01',
    status: 'Auditing',
    audit_version: 1,
    totals_stale: false,
  },
  selected_version: 1,
  versions: [1],
  close_blockers: [],
  cases: [{ id: 'c1', case_number: 'C-1', case_type: 'GL gap', status: 'Open', exposure: '3.00' }],
  closure_requests: [],
  history: [],
  evidence_overrides: [],
  totals: [total({})],
  readiness: {
    ingest: { state: 'Complete', transactions: 5, detail: 'ok' },
    audit: {
      state: 'Needs attention',
      open_cases: 1,
      unexplained_variances: 1,
      detail: '1 open case(s)',
    },
    export: { state: 'Not exported', records: 0, pending: 0, acknowledged: 0 },
    ready_to_close: false,
  },
  data_quality: { state: 'Final', detail: 'Built from data that matches the control total' },
  reopens: [],
  reopen_blocked_by: [],
  ...over,
});

async function open(data: object, me = ME('finance', 'me'), extra = {}) {
  // React's use() reads a promise without suspending only when it already carries its result.
  const value = { storeId: 'S1', date: '2026-10-01' };
  const params = Object.assign(Promise.resolve(value), { status: 'fulfilled' as const, value });
  await renderAs(
    <Suspense fallback="loading">
      <StoreDayDetailPage params={params} />
    </Suspense>,
    me,
    {
      [`GET ${DAY}`]: data,
      [`GET ${DAY}/sequence-gaps`]: { registers: [] },
      ...extra,
    }
  );
  await screen.findByText('S1 · 2026-10-01');
}

describe('Store day detail: completeness and readiness', () => {
  it('lists the transaction numbers missing from each register', async () => {
    await open(detail(), ME('finance'), {
      [`GET ${DAY}/sequence-gaps`]: {
        registers: [{ register_id: 'R1', missing: [4, 9], count: 2, dismissed: [] }],
      },
    });
    expect(await screen.findByText(/2 transaction numbers are missing/)).toBeInTheDocument();
    expect(screen.getByText(/R1: 4, 9/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Review gaps' }));
    expect(screen.getByRole('heading', { name: 'Register R1' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: '4' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: '9' })).toBeInTheDocument();
  });

  it('still opens the day when the gap check is unavailable', async () => {
    await open(detail(), ME('finance'), {
      [`GET ${DAY}/sequence-gaps`]: Object.assign(new Error('x'), { response: { status: 403 } }),
    });
    expect(screen.queryByText(/Transaction numbers are missing/)).not.toBeInTheDocument();
  });

  it('shows data, audit and export readiness for the day', async () => {
    await open(detail());
    const strip = screen.getByLabelText('Store Day readiness');
    expect(within(strip).getByText('Complete')).toBeInTheDocument();
    expect(within(strip).getByText('Needs attention')).toBeInTheDocument();
    expect(within(strip).getByText('Not exported')).toBeInTheDocument();
  });

  it('warns that partial data is not final', async () => {
    await open(
      detail({
        data_quality: { state: 'Partial', detail: 'Built from fewer transactions than expected' },
      })
    );
    expect(screen.getByText(/These totals are partial/)).toBeInTheDocument();
    expect(screen.getByText(/fewer transactions than expected/)).toBeInTheDocument();
  });
});

describe('Store day detail: explaining a variance', () => {
  it('asks for a reason when the variance is outside tolerance, then saves it', async () => {
    await open(detail(), ME('finance'), { [`POST ${DAY}/variance-explanations`]: {} });
    await userEvent.click(screen.getByRole('button', { name: 'Explain…' }));

    expect(screen.getByText(/This variance is outside tolerance/)).toBeInTheDocument();
    const save = screen.getByRole('button', { name: 'Save explanation' });
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText('Reason'), 'Deposit posted next day');
    await userEvent.click(save);

    await waitFor(() => expect(calls('post', `${DAY}/variance-explanations`)).toHaveLength(1));
    expect(calls('post', `${DAY}/variance-explanations`)[0][1]).toEqual({
      total_id: 't-gl',
      reason: 'Deposit posted next day',
      case_id: null,
      correction_id: null,
    });
  });

  it('insists on a linked case or correction above the case threshold', async () => {
    await open(
      detail({ totals: [total({ outcome: 'Needs case', variance_amount: '30.00' })] }),
      ME('finance'),
      {
        [`POST ${DAY}/variance-explanations`]: {},
      }
    );
    await userEvent.click(screen.getByRole('button', { name: 'Explain…' }));
    await userEvent.type(screen.getByLabelText('Reason'), 'Missing deposit');

    const save = screen.getByRole('button', { name: 'Save explanation' });
    expect(save).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText('Case'), 'c1');
    expect(save).toBeEnabled();
    await userEvent.click(save);
    await waitFor(() => expect(calls('post', `${DAY}/variance-explanations`)).toHaveLength(1));
    expect(calls('post', `${DAY}/variance-explanations`)[0][1]).toMatchObject({ case_id: 'c1' });
  });

  it('shows an explained variance with who explained it, and offers no further explanation', async () => {
    const explained = total({
      outcome: 'Needs reason',
      resolved: true,
      explanation: {
        id: 'e',
        audit_version: 1,
        variance_amount: '3.00',
        outcome: 'Needs reason',
        reason: 'Rounding at GL',
        case_id: 'c1',
        correction_id: null,
        explained_by: 'finance.user',
        created_at: '2026-10-02T00:00:00Z',
      },
    });
    await open(detail({ totals: [explained] }));

    expect(screen.getByText('Explained')).toBeInTheDocument();
    expect(screen.getByText(/Rounding at GL · case C-1 · finance\.user/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Explain…' })).not.toBeInTheDocument();
  });

  it('offers no explanation on a variance within tolerance', async () => {
    await open(detail({ totals: [total({ outcome: 'Within tolerance', resolved: true })] }));
    expect(screen.getByText('Within tolerance')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Explain…' })).not.toBeInTheDocument();
  });
});

describe('Store day detail: closing needs a second person', () => {
  const pending = (requestedBy: string) => ({
    closure_requests: [
      {
        id: 'q',
        requested_by: requestedBy,
        evidence_basis: 'Variances explained',
        status: 'Pending',
        reviewed_by: null,
        review_reason: null,
        created_at: '2026-10-02T09:00:00Z',
      },
    ],
  });

  it("disables 'Approve & close' for the person who requested closure", async () => {
    await open(detail(pending('me')), ME('finance', 'me'));
    expect(screen.getByRole('button', { name: 'Approve & close' })).toBeDisabled();
    expect(
      screen.getByText('You requested closure; another user must approve it.')
    ).toBeInTheDocument();
  });

  it('lets someone else approve it', async () => {
    await open(detail(pending('requester')), ME('finance', 'approver'), {
      [`POST ${DAY}/close`]: {},
    });
    const approve = screen.getByRole('button', { name: 'Approve & close' });
    expect(approve).toBeEnabled();
    await userEvent.click(approve);
    await waitFor(() => expect(calls('post', `${DAY}/close`)).toHaveLength(1));
  });

  it('keeps approval disabled while closure blockers remain', async () => {
    await open(
      detail({ ...pending('requester'), close_blockers: ['unresolved reconciliation variance'] }),
      ME('finance', 'approver')
    );
    expect(screen.getByRole('button', { name: 'Approve & close' })).toBeDisabled();
    expect(screen.getByText(/unresolved reconciliation variance/)).toBeInTheDocument();
  });
});

describe('Store day detail: reopening', () => {
  const closed = (over: Record<string, unknown> = {}) =>
    detail({
      store_day: { ...detail().store_day, status: 'Closed' },
      ...over,
    });

  it('explains why a closed day cannot be reopened and disables the button', async () => {
    await open(
      closed({
        reopen_blocked_by: [
          'The accounting period is closed through 2026-10-31; Finance must reopen the period first',
        ],
      })
    );
    await userEvent.click(screen.getByRole('button', { name: 'Reopen…' }));

    expect(screen.getByText(/This Store Day cannot be reopened/)).toBeInTheDocument();
    expect(screen.getByText(/closed through 2026-10-31/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('Reopen rationale'), 'Late deposit');
    expect(screen.getByRole('button', { name: 'Reopen' })).toBeDisabled();
  });

  it('reopens with a rationale when nothing blocks it', async () => {
    await open(closed(), ME('finance'), { [`POST ${DAY}/reopen`]: {} });
    await userEvent.click(screen.getByRole('button', { name: 'Reopen…' }));
    await userEvent.type(screen.getByLabelText('Reopen rationale'), 'Late deposit');
    await userEvent.click(screen.getByRole('button', { name: 'Reopen' }));

    await waitFor(() => expect(calls('post', `${DAY}/reopen`)).toHaveLength(1));
    expect(calls('post', `${DAY}/reopen`)[0][1]).toEqual({ rationale: 'Late deposit' });
  });

  it('lists every earlier reopen with who, why and who had closed it', async () => {
    await open(
      closed({
        reopens: [
          {
            id: 'r1',
            reopened_by: 'finance.user',
            reopened_at: '2026-10-05T10:00:00Z',
            reason: 'Missing deposit slip',
            closed_by: 'closer',
            closed_at: '2026-10-03T10:00:00Z',
            audit_version: 2,
          },
        ],
      })
    );
    await userEvent.click(screen.getByRole('button', { name: 'Reopen…' }));

    expect(screen.getByRole('heading', { name: 'Reopen history (1)' })).toBeInTheDocument();
    const row = screen.getByText('Missing deposit slip').closest('tr') as HTMLElement;
    expect(within(row).getByText('finance.user')).toBeInTheDocument();
    expect(within(row).getByText(/closer/)).toBeInTheDocument();
    expect(within(row).getByText('v2')).toBeInTheDocument();
  });

  it('says a day that was never reopened has no history', async () => {
    await open(closed());
    await userEvent.click(screen.getByRole('button', { name: 'Reopen…' }));
    expect(screen.getByText('This Store Day has never been reopened.')).toBeInTheDocument();
  });
});

describe('Store day detail: organized status and totals', () => {
  const plain = (
    id: string,
    name: string,
    level: string,
    dimension: string | null,
    amount: string
  ) =>
    total({
      id,
      name,
      level,
      dimension,
      calculated_amount: amount,
      declared_amount: null,
      variance_amount: null,
      outcome: null,
      tolerance: undefined,
    });
  const mixed = [
    total({}),
    plain('t-net', 'POS Net', 'Store', null, '100.00'),
    plain('t-card', 'POS Net', 'Tender', 'Card', '60.00'),
    plain('t-r2', 'POS Net', 'Register', 'R2', '40.00'),
    plain('t-r1', 'POS Net', 'Register', 'R1', '60.00'),
  ];

  it('groups totals into balancing, store figures and one table per breakdown', async () => {
    await open(detail({ totals: mixed }));
    const balancing = screen.getByRole('region', { name: 'Balancing' });
    expect(within(balancing).getByText('POS to GL Variance')).toBeInTheDocument();
    expect(within(balancing).getByRole('button', { name: 'Explain…' })).toBeInTheDocument();
    const store = screen.getByRole('region', { name: 'Store totals' });
    expect(within(store).getByText('POS Net')).toBeInTheDocument();
    expect(within(store).queryByRole('columnheader', { name: 'Declared' })).toBeNull();
    const registers = screen.getByRole('region', { name: 'By register' });
    const rows = within(registers).getAllByRole('row').slice(1);
    expect(rows.map((r) => within(r).getAllByRole('cell')[0].textContent)).toEqual([
      'R1POS Net',
      'R2POS Net',
    ]);
    expect(screen.getByRole('region', { name: 'By tender' })).toBeInTheDocument();
  });

  it('narrows to the groups with a variance when asked', async () => {
    await open(detail({ totals: mixed }));
    await userEvent.click(screen.getByRole('button', { name: 'Out of balance 1' }));
    expect(screen.getByRole('region', { name: 'Balancing' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Store totals' })).toBeNull();
    expect(screen.queryByRole('region', { name: 'By register' })).toBeNull();
  });

  it('collects what stands in the way of closure in one list, with the actions in the header', async () => {
    await open(
      detail({
        close_blockers: ['1 open case'],
        store_day: { ...detail().store_day, totals_stale: true },
      })
    );
    const list = screen.getByRole('list', { name: 'Needs attention' });
    expect(screen.getByText('Needs attention (3)')).toBeInTheDocument();
    expect(within(list).getByText('Closure blockers (1)')).toBeInTheDocument();
    expect(within(list).getByText(/New data arrived/)).toBeInTheDocument();
    expect(within(list).getByText(/1 variance needs an explanation/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Re-total' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '1 open of 1' })).toBeInTheDocument();
  });
});
