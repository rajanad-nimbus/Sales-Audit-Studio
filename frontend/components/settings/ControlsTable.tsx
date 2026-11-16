import { DataTable } from '@/components/ui';
import type { Column } from '@/components/ui';
import type { ReactNode } from 'react';

type ControlsTableProps = {
  headers: string[];
  rows: ReactNode[][];
};

export default function ControlsTable({ headers, rows }: ControlsTableProps) {
  const tableRows = rows.map((cells, index) => ({
    id: String(cells[0]) || `row-${index}`,
    cells,
  }));
  const columns: Column<(typeof tableRows)[number]>[] = headers.map((header, i) => ({
    key: header,
    header,
    render: (row) => row.cells[i],
  }));
  return (
    <div className="mt-md">
      <DataTable
        columns={columns}
        rows={tableRows}
        rowKey={(row) => row.id}
        emptyTitle="No records configured."
      />
    </div>
  );
}
