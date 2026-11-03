import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DataFeeds } from '@/components/diagnostics/DataFeeds';
import { apiFailure, mockApi } from '@/test/utils';

const feed = (over: object) => ({
  source_system: 'POS',
  state: 'Current',
  last_received_at: '2026-10-10T10:00:00Z',
  last_accepted_at: '2026-10-10T10:00:00Z',
  received_24h: 120,
  quarantined_24h: 0,
  quarantined_total: 0,
  ...over,
});
const FEEDS = {
  stale_after_hours: 48,
  feeds: [
    feed({}),
    feed({ source_system: 'Bank', state: 'Stale', last_accepted_at: '2026-10-01T10:00:00Z' }),
    feed({
      source_system: 'ERP',
      state: 'No data',
      last_received_at: null,
      last_accepted_at: null,
      received_24h: 4,
      quarantined_24h: 4,
      quarantined_total: 9,
    }),
  ],
  quarantine_reasons_7d: [{ source_system: 'ERP', reason: 'missing store_id', records: 4 }],
};

describe('DataFeeds', () => {
  it('shows the state of every feed and the limit that makes one stale', async () => {
    mockApi({ 'GET /api/diagnostics/feeds': FEEDS });
    render(<DataFeeds />);

    const pos = (await screen.findByText('POS')).closest('tr') as HTMLElement;
    expect(within(pos).getByText('Current')).toBeInTheDocument();
    expect(within(pos).getByText('120')).toBeInTheDocument();
    const bank = screen.getByText('Bank').closest('tr') as HTMLElement;
    expect(within(bank).getByText('Stale')).toBeInTheDocument();
    expect(screen.getByText(/Stale after 48 hours/)).toBeInTheDocument();
  });

  it('flags records set aside in the last day, and says "Never" for a feed that sent nothing accepted', async () => {
    mockApi({ 'GET /api/diagnostics/feeds': FEEDS });
    render(<DataFeeds />);
    await screen.findByText('POS');
    const [feedsTable] = screen.getAllByRole('table');
    const erp = within(feedsTable).getByText('ERP').closest('tr') as HTMLElement;

    expect(within(erp).getByText('No data')).toBeInTheDocument();
    expect(within(erp).getAllByText('Never')).toHaveLength(2);
    expect(within(erp).getByText('9')).toBeInTheDocument();
  });

  it('lists why records were set aside', async () => {
    mockApi({ 'GET /api/diagnostics/feeds': FEEDS });
    render(<DataFeeds />);
    expect(await screen.findByText('missing store_id')).toBeInTheDocument();
    expect(screen.getByText('Why records were set aside, last 7 days')).toBeInTheDocument();
  });

  it('shows no reasons section when nothing was set aside', async () => {
    mockApi({ 'GET /api/diagnostics/feeds': { ...FEEDS, quarantine_reasons_7d: [] } });
    render(<DataFeeds />);
    await screen.findByText('POS');
    expect(screen.queryByText(/Why records were set aside/)).not.toBeInTheDocument();
  });

  it('shows why the feeds could not be loaded', async () => {
    mockApi({ 'GET /api/diagnostics/feeds': apiFailure('Forbidden', 403) });
    render(<DataFeeds />);
    expect(await screen.findByText('Forbidden')).toBeInTheDocument();
  });
});
