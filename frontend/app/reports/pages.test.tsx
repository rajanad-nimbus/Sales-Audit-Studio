import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import ErrorsPage from '@/app/reports/errors/page';
import StoreDaysPage from '@/app/reports/store-days/page';
import TaxCorrectionsPage from '@/app/reports/tax-corrections/page';
import TaxPage from '@/app/reports/tax/page';
import VariancesPage from '@/app/reports/variances/page';
import { REPORT_PAGES } from '@/lib/reports';
import { api } from '@/lib/api';
import { calls, mockApi } from '@/test/utils';

const EMPTY = { page: 1, page_size: 50, columns: ['Store'], rows: [], has_more: false };

describe('each report is its own page that loads only its own data', () => {
  const pages = [
    ['variances', VariancesPage, REPORT_PAGES.variances],
    ['errors', ErrorsPage, REPORT_PAGES.errors],
    ['store-days', StoreDaysPage, REPORT_PAGES.storeDays],
    ['tax', TaxPage, REPORT_PAGES.tax],
    ['tax-corrections', TaxCorrectionsPage, REPORT_PAGES.taxCorrections],
  ] as const;

  it.each(pages)(
    '/reports/%s shows its title and requests only that report',
    async (name, Page, spec) => {
      mockApi(Object.fromEntries(pages.map(([n]) => [`GET /api/reports/${n}`, EMPTY])));
      render(<Page />);

      expect(await screen.findByRole('heading', { name: spec.title })).toBeInTheDocument();
      await screen.findByText('Nothing to report for this range');
      expect(calls('get', `/api/reports/${name}`)).toHaveLength(1);
      const others = pages
        .filter(([n]) => n !== name)
        .flatMap(([n]) => calls('get', `/api/reports/${n}`));
      expect(others).toHaveLength(0);
      // Besides its own report, a page only loads the store list for its store filter.
      const requested = (api.get as unknown as { mock: { calls: [string][] } }).mock.calls.map(
        ([path]) => path
      );
      expect(requested.sort()).toEqual([`/api/reports/${name}`, '/api/stores'].sort());
    }
  );

  it('gives every report its own address and title', () => {
    const paths = Object.values(REPORT_PAGES).map((r) => r.path);
    expect(new Set(paths).size).toBe(5);
    expect(paths).toEqual([
      '/reports/variances',
      '/reports/errors',
      '/reports/store-days',
      '/reports/tax',
      '/reports/tax-corrections',
    ]);
    expect(new Set(Object.values(REPORT_PAGES).map((r) => r.title)).size).toBe(5);
  });
});
