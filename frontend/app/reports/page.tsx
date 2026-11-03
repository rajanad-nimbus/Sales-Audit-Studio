import { redirect } from 'next/navigation';
import { REPORT_PAGES } from '@/lib/reports';

/** There is no combined report: each report is its own page, so /reports opens the first. */
export default function ReportsIndex() {
  redirect(REPORT_PAGES.variances.path);
}
