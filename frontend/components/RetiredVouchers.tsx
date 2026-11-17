import { DataTable } from '@/components/ui';
import { money } from '@/lib/api';
import { formatBusinessDate, formatDateTime } from '@/lib/preferences';

export interface RetiredVoucher {
  voucher_number: string;
  issued_store_id: string;
  tender_type: string;
  issued_on: string;
  amount: string;
  currency: string;
  final_status: string;
  finalized_on: string;
  retired_at: string;
  retention_run_id: string;
}

export function RetiredVouchers({ rows }: { rows: RetiredVoucher[] | null }) {
  return (
    <>
      <p className="hint">
        These settled vouchers have been archived and purged from the live ledger. Their numbers
        remain permanently reserved.
      </p>
      <DataTable
        rows={rows}
        rowKey={(row) => row.voucher_number}
        emptyTitle="No retired vouchers"
        csvFileName="retired-vouchers-visible.csv"
        columns={[
          {
            key: 'number',
            header: 'Voucher',
            primary: true,
            render: (row) => row.voucher_number,
            csvValue: (row) => row.voucher_number,
          },
          {
            key: 'store',
            header: 'Issued at',
            render: (row) => row.issued_store_id,
            csvValue: (row) => row.issued_store_id,
          },
          {
            key: 'tender',
            header: 'Tender',
            render: (row) => row.tender_type,
            csvValue: (row) => row.tender_type,
          },
          {
            key: 'amount',
            header: 'Amount',
            numeric: true,
            render: (row) => money(row.amount, row.currency),
            csvValue: (row) => `${row.currency} ${row.amount}`,
          },
          {
            key: 'status',
            header: 'Final status',
            render: (row) => `${row.final_status} · ${formatBusinessDate(row.finalized_on)}`,
            csvValue: (row) => `${row.final_status} · ${row.finalized_on}`,
          },
          {
            key: 'retired',
            header: 'Retired',
            render: (row) => formatDateTime(row.retired_at),
            csvValue: (row) => row.retired_at,
          },
          {
            key: 'run',
            header: 'Archive run',
            render: (row) => row.retention_run_id,
            csvValue: (row) => row.retention_run_id,
          },
        ]}
      />
    </>
  );
}
