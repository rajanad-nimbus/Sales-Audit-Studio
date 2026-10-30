import { DataTable } from '@/components/ui';
import type { Column } from '@/components/ui';

type ControlsTableProps = {
  headers: string[];
  rows: string[][];
};

export default function ControlsTable({ headers, rows }: ControlsTableProps) {
  const columns: Column<string[]>[] = headers.map((header, i) => ({
    key: header,
    header,
    render: (row) => row[i],
  }));
  return (
    <div className="mt-md">
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(row) => row.join('|')}
        emptyTitle="No records configured."
      />
    </div>
  );
}
