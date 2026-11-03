import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CommandCenter } from '@/components/CommandCenter';
import { ME, apiFailure, calls, renderAs } from '@/test/utils';

const NO_CASES = { items: [], total: 0 };
const routes = (extra = {}) => ({
  'GET /api/action-center/summary': {
    open_amount: '1200.00',
    open_exposure: '800.00',
    currency: 'USD',
    amounts_by_currency: [],
    stale_amount_cases: 0,
    needs_decision: 7,
    closed: 12,
    agent_prepared: 4,
    agent_waiting_evidence: 0,
  },
  'GET /api/action-center/facets?stage=': { status: [] },
  'GET /api/action-center/cases?stage=decision&sort=exposure&dir=desc&page_size=5': NO_CASES,
  'GET /api/action-center/cases?stage=progress&sort=sla&dir=asc&page_size=5': NO_CASES,
  'GET /api/action-center/cases?status=Closed&sort=age&dir=asc&page_size=5': NO_CASES,
  'GET /api/connectors': [],
  'GET /api/command-center/plan': {
    as_of: '2026-10-10T12:00:00Z',
    data: {
      status: 'critical',
      batch: {
        freshness: 'fresh',
        last_failed: true,
        hours_since_success: 30,
        data_as_of: '2026-10-08',
      },
      silent_feeds: ['Bank'],
      feeds: 6,
    },
    store_days: {
      open: 9,
      examined: 9,
      sampled: false,
      ready_to_close: 3,
      data_incomplete: 1,
      needs_attention: 2,
      stuck: 0,
      stuck_after_days: 3,
      late_stores: 1,
      expected_stores: 10,
      late_date: '2026-10-09',
    },
  },
  ...extra,
});

describe('Command Center: operating plan', () => {
  it('shows live data readiness and store-day state instead of fixed text', async () => {
    await renderAs(<CommandCenter />, ME('finance'), routes());

    const dataStage = await screen.findByRole('link', { name: /Confirm data arrived/ });
    expect(await within(dataStage).findByText('Batch failed')).toBeInTheDocument();
    const dayStage = screen.getByRole('link', { name: /Balance Store Days/ });
    expect(within(dayStage).getByText('Held back')).toBeInTheDocument();
    expect(within(dayStage).getByText('3')).toBeInTheDocument();
    expect(
      within(dayStage).getByText(/ready to close · 2 need attention · 1 store late for 2026-10-09/)
    ).toBeInTheDocument();
    expect(screen.queryByText('POS and simulated feeds')).not.toBeInTheDocument();
  });

  it('asks for the plan once on arrival', async () => {
    await renderAs(<CommandCenter />, ME('finance'), routes());
    await screen.findByText('Batch failed');
    expect(calls('get', '/api/command-center/plan')).toHaveLength(1);
  });

  it('still works when the plan cannot be loaded, and does not call the page unreachable', async () => {
    await renderAs(
      <CommandCenter />,
      ME('finance'),
      routes({ 'GET /api/command-center/plan': apiFailure('Server error', 500) })
    );
    await waitFor(() => expect(screen.getAllByText('Status unavailable')).toHaveLength(2));
    expect(screen.queryByText(/Backend unreachable/)).not.toBeInTheDocument();
    // The decision stage still loaded its count.
    const decisions = await screen.findByRole('link', { name: /Your decisions/ });
    await waitFor(() => expect(within(decisions).getByText('7')).toBeInTheDocument());
  });
});
