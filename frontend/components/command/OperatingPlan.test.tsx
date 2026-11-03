import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { OperatingPlan, dataStage, storeDayStage } from '@/components/command/OperatingPlan';
import type { Plan } from '@/components/command/OperatingPlan';

const data = (
  over: Partial<Plan['data']> = {},
  batch: Partial<Plan['data']['batch']> = {}
): Plan['data'] => ({
  status: 'ok',
  batch: {
    freshness: 'fresh',
    last_failed: false,
    hours_since_success: 3,
    data_as_of: '2026-10-09',
    ...batch,
  },
  silent_feeds: [],
  feeds: 6,
  ...over,
});
const days = (over: Partial<Plan['store_days']> = {}): Plan['store_days'] => ({
  open: 10,
  examined: 10,
  sampled: false,
  ready_to_close: 6,
  data_incomplete: 0,
  needs_attention: 0,
  stuck: 0,
  stuck_after_days: 3,
  late_stores: 0,
  expected_stores: 12,
  late_date: '2026-10-09',
  ...over,
});
const plan = (d = data(), s = days()): Plan => ({
  as_of: '2026-10-10T12:00:00Z',
  data: d,
  store_days: s,
});

describe('dataStage', () => {
  it('is on track when the batch is fresh and every feed has sent data', () => {
    expect(dataStage(data())).toMatchObject({
      tone: 'success',
      label: 'On track',
      detail: 'Data through 2026-10-09',
    });
  });

  it('names the silent feeds, and how many more when there are several', () => {
    const few = dataStage(data({ silent_feeds: ['Bank'] }));
    expect(few).toMatchObject({ tone: 'warning', label: 'Late', headline: '1' });
    expect(few.detail).toBe('1 feed silent: Bank');
    const many = dataStage(data({ silent_feeds: ['POS', 'Bank', 'ERP', 'Shopify', 'Processor'] }));
    expect(many.detail).toBe('5 feeds silent: POS, Bank, ERP and 2 more');
  });

  it('puts a failed batch first, whatever else is wrong', () => {
    const failed = dataStage(data({ silent_feeds: ['Bank'] }, { last_failed: true }));
    expect(failed).toMatchObject({ tone: 'error', label: 'Batch failed', headline: '2' });
    expect(failed.detail).toContain('Silent: Bank');
    expect(dataStage(data({}, { last_failed: true })).detail).toContain('The last batch failed');
  });

  it('says when the batch has never run or has gone stale', () => {
    const never = dataStage(
      data({}, { freshness: 'never', data_as_of: null, hours_since_success: null })
    );
    expect(never).toMatchObject({ tone: 'warning', label: 'Not run' });
    expect(never.detail).toBe('The daily batch has not run yet');
    const stale = dataStage(data({}, { freshness: 'stale', hours_since_success: 52.4 }));
    expect(stale).toMatchObject({ tone: 'warning', label: 'Stale' });
    expect(stale.detail).toBe('Last successful batch 52 hours ago');
  });
});

describe('storeDayStage', () => {
  it('is on track when nothing is held back', () => {
    expect(storeDayStage(days())).toMatchObject({
      tone: 'success',
      label: 'On track',
      detail: '10 open, none held back',
    });
  });

  it('lists what holds days back, joined in one line', () => {
    const held = storeDayStage(days({ needs_attention: 3, late_stores: 2 }));
    expect(held).toMatchObject({ tone: 'warning', label: 'Held back' });
    expect(held.detail).toBe('3 need attention · 2 stores late for 2026-10-09');
    expect(storeDayStage(days({ late_stores: 1 })).detail).toBe('1 store late for 2026-10-09');
  });

  it('treats a day open past the limit as stuck, the most serious state', () => {
    const stuck = storeDayStage(days({ stuck: 4, needs_attention: 1 }));
    expect(stuck).toMatchObject({ tone: 'error', label: 'Stuck' });
    expect(stuck.detail).toBe('1 need attention · 4 open over 3 days');
  });

  it('is on track with nothing open, and says when only the newest days were examined', () => {
    expect(storeDayStage(days({ open: 0, ready_to_close: 0 })).detail).toBe(
      'No store day is waiting'
    );
    expect(
      storeDayStage(days({ open: 900, examined: 500, sampled: true, needs_attention: 5 })).detail
    ).toBe('5 need attention (newest 500 of 900)');
  });

  it('counts incomplete data as holding a day back even with nothing else wrong', () => {
    expect(storeDayStage(days({ data_incomplete: 2 })).tone).toBe('warning');
  });
});

const props = (over = {}) => ({
  plan: plan(),
  prepared: 4,
  decisions: 7,
  awaitingEvidence: 0,
  onShowPrepared: vi.fn(),
  ...over,
});

describe('OperatingPlan', () => {
  it('shows real numbers for data readiness and store days, and links to where each is handled', () => {
    render(
      <OperatingPlan
        {...props({ plan: plan(data({ silent_feeds: ['Bank'] }), days({ needs_attention: 2 })) })}
      />
    );

    const dataLink = screen.getByRole('link', { name: /Confirm data arrived/ });
    expect(dataLink).toHaveAttribute('href', '/imports');
    expect(within(dataLink).getByText('Late')).toBeInTheDocument();
    expect(within(dataLink).getByText('1 feed silent: Bank')).toBeInTheDocument();

    const dayLink = screen.getByRole('link', { name: /Balance Store Days/ });
    expect(dayLink).toHaveAttribute('href', '/store-days');
    expect(within(dayLink).getByText('Held back')).toBeInTheDocument();
    expect(within(dayLink).getByText('6')).toBeInTheDocument();
    expect(within(dayLink).getByText(/ready to close · 2 need attention/)).toBeInTheDocument();
  });

  it('keeps the agent and decision stages as before', async () => {
    const onShowPrepared = vi.fn();
    render(<OperatingPlan {...props({ onShowPrepared })} />);
    expect(screen.getByRole('link', { name: /Your decisions/ })).toHaveAttribute(
      'href',
      '/actions'
    );
    await userEvent.click(screen.getByRole('button', { name: /Agent prepared/ }));
    expect(onShowPrepared).toHaveBeenCalledTimes(1);
    expect(screen.getByText('4')).toBeInTheDocument();
    expect(screen.getByText('7')).toBeInTheDocument();
  });

  it('says it is checking until the plan arrives, and when it could not be loaded', () => {
    const { rerender } = render(<OperatingPlan {...props({ plan: null })} />);
    expect(screen.getAllByText('Checking…')).toHaveLength(2);
    rerender(<OperatingPlan {...props({ plan: undefined })} />);
    expect(screen.getAllByText('Status unavailable')).toHaveLength(2);
    // The agent and decision stages do not wait for the plan.
    expect(screen.getByText('7')).toBeInTheDocument();
  });

  it('mentions cases waiting for evidence only when there are some', () => {
    const { rerender } = render(<OperatingPlan {...props()} />);
    expect(screen.queryByText(/waiting for evidence/)).not.toBeInTheDocument();
    rerender(<OperatingPlan {...props({ awaitingEvidence: 1 })} />);
    expect(screen.getByText(/waiting for evidence on 1 case;/)).toBeInTheDocument();
    rerender(<OperatingPlan {...props({ awaitingEvidence: 3 })} />);
    expect(screen.getByText(/waiting for evidence on 3 cases;/)).toBeInTheDocument();
  });
});
