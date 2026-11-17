import { ReportView } from '@/components/reports/ReportView';
import { REPORT_PAGES } from '@/lib/reports';

export default function FlashTotalsPage() {
  return <ReportView report={REPORT_PAGES.flashTotals} />;
}
