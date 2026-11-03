import { ReportView } from '@/components/reports/ReportView';
import { REPORT_PAGES } from '@/lib/reports';

export default function Page() {
  return <ReportView report={REPORT_PAGES.errors} />;
}
