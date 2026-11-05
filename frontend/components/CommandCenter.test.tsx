import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { CommandCenter } from '@/components/CommandCenter';
import { ME, apiFailure, calls, renderAs } from '@/test/utils';
import { snapshot, writeSeen } from '@/lib/commandCenterSeen';

const counts = {
  needs_decision: 4,
  agent_prepared: 2,
  agent_waiting_evidence: 1,
  sla_breached: 3,
  at_risk: 5,
  unassigned: 6,
  mine: 1,
  snoozed: 0,
  open_total: 12,
  stores_affected: 7,
  open_exposure: '100.00',
  currency: 'USD',
  amounts_by_currency: [],
  straight_through_rate: 80,
  by_day: [
    { date: '2026-10-31', count: 2 },
    { date: '2026-11-01', count: 5 },
  ],
};

const queueCase = {
  id: 'c1',
  case_number: 'C-1',
  case_type: 'Cash variance',
  store_id: 'S1',
  currency: 'USD',
  amount_needs_review: false,
  business_date: '2026-11-01',
  status: 'Open',
  priority: 'High',
  exposure: '25.00',
  sla_state: 'breached',
  assigned_to: null,
};

const overview = (over: object = {}) => ({
  generated_at: '2026-11-03T00:00:00Z',
  freshness: { freshness: 'fresh', data_as_of: '2026-11-02' },
  work: {
    counts,
    queue: [queueCase],
    hotspots: {
      stores: [{ name: 'S1', open: 4, past_sla: 1 }],
      types: [{ name: 'Cash variance', open: 4, past_sla: 0 }],
    },
    mine: [{ ...queueCase, id: 'c2', case_number: 'C-2', assigned_to: 'tester' }],
  },
  store_days: null,
  audit_errors: null,
  exports: { failed: 2, pending: 0, awaiting_ack: 1 },
  health: { error: 'This section could not be loaded' },
  feeds: {
    stale_after_hours: 24,
    feeds: [
      {
        source_system: 'Shopify',
        state: 'Stale',
        last_accepted_at: null,
        received_24h: 0,
        quarantined_total: 3,
      },
    ],
    quarantine_reasons: [],
  },
  cash: {
    window_days: 7,
    declarations: 10,
    out_of_balance: 2,
    short: '15.00',
    over: '4.00',
    vouchers_outstanding: [{ currency: 'USD', vouchers: 3, amount: '60.00' }],
    vouchers_expired: [],
  },
  activity: { events: [] },
  ...over,
});

describe('CommandCenter', () => {
  beforeEach(() => localStorage.clear());

  it('shows counts, the work queue and each section, and hides ones the role cannot see', async () => {
    await renderAs(<CommandCenter />, ME('finance'), { 'GET /api/command-center': overview() });
    expect(await screen.findByText('Needs decision')).toBeTruthy();
    expect(screen.getByText('C-1')).toBeTruthy();
    expect(screen.getByText('Data as of Nov 2')).toBeTruthy();
    expect(screen.getByText('Exports')).toBeTruthy();
    expect(screen.getByText('1 past SLA')).toBeTruthy();
    expect(screen.queryByText('Store days')).toBeNull();
    expect(screen.getByText('This section could not be loaded')).toBeTruthy();
  });

  it('shows every group on one page, with no tabs', async () => {
    await renderAs(<CommandCenter />, ME('finance'), { 'GET /api/command-center': overview() });
    expect(await screen.findByText('Assigned to you (1)')).toBeTruthy();
    expect(screen.getByText('C-2')).toBeTruthy();
    expect(screen.getByText('Where work is piling up')).toBeTruthy();
    expect(screen.getByText('Stores and cash')).toBeTruthy();
    expect(screen.getByText('Short')).toBeTruthy();
    expect(screen.getByText('Recent activity')).toBeTruthy();
    expect(screen.getByText('No activity recorded yet.')).toBeTruthy();
    expect(screen.getByText(/^Shopify: stale/)).toBeTruthy();
    expect(screen.queryByRole('tab')).toBeNull();
  });

  it('renders store days and audit errors when permitted and present', async () => {
    const data = overview({
      store_days: {
        business_date: '2026-11-02',
        expected_stores: 10,
        late_total: 2,
        late_stores: [{ store_id: 'S2', state: 'Missing TLOG' }],
        stuck_total: 1,
        stuck_truncated: false,
        stuck: [
          {
            store_id: 'S3',
            business_date: '2026-10-30',
            status: 'Audit in progress',
            days_open: 4,
            holding_back: ['Missing tender declaration'],
          },
        ],
      },
      audit_errors: {
        open_total: 5,
        open_types: 2,
        window_days: 30,
        top: [
          {
            code: 'ERR_TENDER',
            label: 'Tender Discrepancy',
            open_count: 3,
            open_by_currency: [],
          },
        ],
      },
    });

    await renderAs(<CommandCenter />, ME('admin'), { 'GET /api/command-center': data });
    expect(await screen.findByText('Store days')).toBeTruthy();
    expect(screen.getByText('2 of 10 late for Nov 2')).toBeTruthy();
    expect(screen.getByText(/S3, Oct 30: open 4 days\. Missing tender declaration/)).toBeTruthy();
    expect(screen.getByText('Missing TLOG')).toBeTruthy();
    expect(screen.getByText('Audit errors')).toBeTruthy();
    expect(screen.getByText('5 open in 2 types')).toBeTruthy();
    expect(screen.getByText('Tender Discrepancy: 3')).toBeTruthy();
  });

  it('leads with the situation and shows exposure and straight-through rate', async () => {
    await renderAs(<CommandCenter />, ME('finance'), { 'GET /api/command-center': overview() });
    expect(
      await screen.findByText(
        '3 cases are past SLA, 4 waiting for a decision and 5 due within 8 hours.'
      )
    ).toBeTruthy();
    expect(screen.getByText('Exposure', { selector: 'dt' })).toBeTruthy();
    expect(screen.getByText('$100.00')).toBeTruthy();
    expect(screen.getByText('Closed without manual work')).toBeTruthy();
    expect(screen.getByText('80%')).toBeTruthy();
  });

  it('shows error alert and retry button when command-center fails to load', async () => {
    await renderAs(<CommandCenter />, ME('finance'), {
      'GET /api/command-center': apiFailure('Server error', 500),
    });
    expect(
      await screen.findByText(
        'The Command Center could not be loaded. Check your connection and retry.'
      )
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });

  describe('actions', () => {
    const screens = (...paths: string[]) =>
      paths.map((path) => ({ key: path, path, label: path, group: 'x', allowed: true, roles: [] }));
    const as = (role: string, ...paths: string[]) =>
      ME(role, 'tester', { screens: screens('/', ...paths) });

    it('assigns an unassigned case to me from the work queue', async () => {
      const user = userEvent.setup();
      await renderAs(<CommandCenter />, as('it', '/actions'), {
        'GET /api/command-center': overview(),
        'POST /api/action-center/bulk': { ok: 1, skipped: [] },
      });
      await user.click(await screen.findByRole('button', { name: 'Assign C-1 to me' }));
      await waitFor(() =>
        expect(calls('post', '/api/action-center/bulk')[0][1]).toEqual({
          action: 'assign',
          ids: ['c1'],
          params: { assignee: 'me' },
        })
      );
      expect(await screen.findByText('C-1 is assigned to you.')).toBeTruthy();
    });

    it('approves only the agent-nominated cases, after confirmation', async () => {
      const user = userEvent.setup();
      await renderAs(<CommandCenter />, as('finance', '/actions'), {
        'GET /api/command-center': overview(),
        'GET /api/agent/eligible': {
          count: 2,
          cases: [
            { case_id: 'e1', case_number: 'C-11', amount: '4.00', currency: 'USD' },
            { case_id: 'e2', case_number: 'C-12', amount: '2.50', currency: 'USD' },
          ],
        },
        'POST /api/action-center/bulk': { ok: 2, skipped_count: 0, skipped: [] },
      });
      await user.click(await screen.findByRole('button', { name: 'Review and approve 2' }));
      expect(screen.getByText(/C-11, exception \$4\.00/)).toBeTruthy();
      expect(calls('post', '/api/action-center/bulk')).toHaveLength(0);
      await user.click(screen.getByRole('button', { name: 'Approve 2 cases' }));
      await waitFor(() =>
        expect(calls('post', '/api/action-center/bulk')[0][1]).toEqual({
          action: 'approve',
          ids: ['e1', 'e2'],
        })
      );
      expect(await screen.findByText('Approved 2 cases.')).toBeTruthy();
    });

    it('retries a failed export after confirmation', async () => {
      const user = userEvent.setup();
      const exports = {
        failed: 1,
        pending: 0,
        awaiting_ack: 0,
        failed_batches: [
          {
            id: 'b1',
            dataset: 'gl',
            destination: 'Oracle GL',
            error: 'HTTP 503',
            created_at: '2026-11-03T00:00:00Z',
            retries: 0,
          },
        ],
      };
      await renderAs(<CommandCenter />, as('it', '/exports'), {
        'GET /api/command-center': overview({ exports }),
        'POST /api/exports/batches/b1/retry': { status: 'Sent' },
      });
      expect(await screen.findByText(/Oracle GL, gl: HTTP 503/)).toBeTruthy();
      await user.click(screen.getByRole('button', { name: 'Retry' }));
      await user.click(screen.getByRole('button', { name: 'Retry delivery' }));
      await waitFor(() => expect(calls('post', '/api/exports/batches/b1/retry')).toHaveLength(1));
      expect(await screen.findByText('Delivered to Oracle GL.')).toBeTruthy();
    });

    it('reruns a failed batch for its business date after confirmation', async () => {
      const user = userEvent.setup();
      const freshness = {
        freshness: 'stale',
        data_as_of: '2026-10-31',
        hours_since_success: 30,
        last_failed: true,
        last_run: { business_date: '2026-11-01', status: 'Failed' },
      };
      await renderAs(<CommandCenter />, as('it', '/imports'), {
        'GET /api/command-center': overview({ freshness }),
        'POST /api/batch/run': { status: 'Succeeded' },
      });
      await user.click(await screen.findByRole('button', { name: 'Rerun Nov 1' }));
      await user.click(screen.getByRole('button', { name: 'Rerun batch' }));
      await waitFor(() =>
        expect(calls('post', '/api/batch/run')[0][1]).toEqual({
          business_date: '2026-11-01',
          rerun: true,
        })
      );
    });

    it('offers no actions to a role that cannot take them', async () => {
      await renderAs(<CommandCenter />, as('auditor', '/actions', '/exports', '/imports'), {
        'GET /api/command-center': overview(),
      });
      expect(await screen.findByText('C-1')).toBeTruthy();
      expect(screen.queryByRole('button', { name: /Assign .* to me/ })).toBeNull();
      expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
      expect(screen.queryByRole('button', { name: /Review and approve/ })).toBeNull();
    });
  });

  it('shows how far the business date is from closed, and what blocks it', async () => {
    const close = {
      business_date: '2026-11-02',
      expected_stores: 10,
      opened: 9,
      closed: 5,
      ready: 1,
      blocked: 3,
      unchecked: 0,
      blocked_reasons: [{ reason: 'Needs attention', store_days: 2 }],
      blocked_examples: [{ store_id: 'S7', reasons: ['Audit: 2 open case(s)'] }],
      export: { Final: 3, 'Not exported': 2 },
    };
    await renderAs(<CommandCenter />, ME('finance'), {
      'GET /api/command-center': overview({ close }),
    });
    expect(await screen.findByText('Close for Nov 2')).toBeTruthy();
    expect(screen.getByText(/1 ready to close, 3 blocked, 1 with no store day yet\./)).toBeTruthy();
    expect(screen.getByText('Needs attention')).toBeTruthy();
    expect(screen.getByText(/S7 \(Audit: 2 open case\(s\)\)/)).toBeTruthy();
    expect(screen.getByText('Acknowledged')).toBeTruthy();
    expect(screen.getByText('Not exported yet')).toBeTruthy();
  });

  it('says what changed since the last visit and marks new rows', async () => {
    const user = userEvent.setup();
    writeSeen(
      'tester',
      snapshot(
        {
          counts: { ...counts, sla_breached: 1, needs_decision: 6 },
          queue: [{ id: 'old', sla_state: 'ok' }],
          failedExports: [],
          feeds: [],
          close: null,
        },
        new Date('2026-11-03T09:14:00')
      )
    );
    await renderAs(<CommandCenter />, ME('finance'), { 'GET /api/command-center': overview() });
    expect(await screen.findByText('2 more cases past SLA')).toBeTruthy();
    expect(screen.getByText('2 fewer cases waiting for a decision')).toBeTruthy();
    expect(screen.getByText('Shopify fell behind')).toBeTruthy();
    expect(screen.getByText(/2 more since/)).toBeTruthy();
    expect(screen.getAllByText('New')).toHaveLength(2);
    await user.click(screen.getByRole('button', { name: 'Mark as seen' }));
    expect(screen.queryByText('2 more cases past SLA')).toBeNull();
    expect(screen.queryByText('New')).toBeNull();
  });

  it('shows no comparison on a first visit', async () => {
    await renderAs(<CommandCenter />, ME('finance'), { 'GET /api/command-center': overview() });
    expect(await screen.findByText('C-1')).toBeTruthy();
    expect(screen.queryByText(/^Since /)).toBeNull();
    expect(screen.queryByText('New')).toBeNull();
  });
});
