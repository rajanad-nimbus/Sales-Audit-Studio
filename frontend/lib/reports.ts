/** The report pages. Each is its own route and loads only its own data. */
export interface ReportPage {
  /** The report's name in /api/reports/{name}. */
  name: string;
  title: string;
  subtitle: string;
  path: string;
}

const page = (name: string, title: string, subtitle: string): ReportPage => ({
  name,
  title,
  subtitle,
  path: `/reports/${name}`,
});

export const REPORT_PAGES = {
  variances: page(
    'variances',
    'Total variances',
    'Totals that do not balance on each store day, with the tolerance outcome and whether each is explained.'
  ),
  errors: page(
    'errors',
    'Errors by store',
    'Audit errors by store and error code, with how many are still open.'
  ),
  storeDays: page(
    'store-days',
    'Store day status',
    'Data, audit and export readiness for each store day.'
  ),
  tax: page('tax', 'Tax / VAT', 'Tax collected by store, day and tax code. Returns net off.'),
  taxCorrections: page(
    'tax-corrections',
    'Tax corrections',
    "Approved corrections to a transaction's tax, one row each, with who approved them."
  ),
} satisfies Record<string, ReportPage>;
