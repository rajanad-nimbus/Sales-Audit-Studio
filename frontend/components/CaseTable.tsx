'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Case, caseMoney } from '@/lib/api';
import { DataTable } from '@/components/ui';
import type { Column } from '@/components/ui';

const STATUS_TONE: Record<string, string> = {
  Open: 'badge-secondary',
  'In Investigation': 'badge-info',
  'In Review': 'badge-warning',
  Resolving: 'badge-primary',
  'Pending Validation': 'badge-primary',
  Closed: 'badge-success',
};

export const PriorityBadge = ({ p }: { p: string }) => (
  <span
    className={`badge badge-sm ${p === 'Critical' || p === 'High' ? 'badge-error' : 'badge-secondary'}`}
  >
    {p}
  </span>
);

export const StatusBadge = ({ s }: { s: string }) => (
  <span className={`badge badge-sm ${STATUS_TONE[s] ?? 'badge-secondary'}`}>{s}</span>
);

export const Evidence = ({ pct }: { pct: number }) => (
  <span className="ev-cell">
    <span className={`ev-bar ${pct >= 100 ? 'full' : ''}`} aria-hidden>
      <i style={{ width: `${Math.min(100, pct)}%` }} />
    </span>
    {pct}%
  </span>
);

export type SortKey =
  | 'case_number'
  | 'case_type'
  | 'store_id'
  | 'total_exception_amount'
  | 'total_exposure'
  | 'evidence_completeness'
  | 'priority'
  | 'status';
export interface SortState {
  key: SortKey;
  dir: 'asc' | 'desc';
}

const COLUMNS: Column<Case>[] = [
  {
    key: 'case_number',
    header: 'Case',
    sortKey: 'case_number',
    primary: true,
    stopRowClick: true,
    render: (c) => <Link href={`/cases/${c.id}`}>{c.case_number}</Link>,
  },
  { key: 'case_type', header: 'Type', sortKey: 'case_type', render: (c) => c.case_type },
  { key: 'store_id', header: 'Store', sortKey: 'store_id', render: (c) => c.store_id },
  {
    key: 'total_exception_amount',
    header: 'Exception',
    sortKey: 'total_exception_amount',
    numeric: true,
    render: (c) =>
      c.amount_needs_review ? 'Review required' : caseMoney(c.total_exception_amount, c.currency),
  },
  {
    key: 'total_exposure',
    header: 'Exposure',
    sortKey: 'total_exposure',
    numeric: true,
    render: (c) =>
      c.amount_needs_review ? 'Review required' : caseMoney(c.total_exposure, c.currency),
  },
  {
    key: 'evidence_completeness',
    header: 'Evidence',
    sortKey: 'evidence_completeness',
    numeric: true,
    render: (c) => <Evidence pct={c.evidence_completeness} />,
  },
  {
    key: 'priority',
    header: 'Priority',
    sortKey: 'priority',
    render: (c) => <PriorityBadge p={c.priority} />,
  },
  {
    key: 'status',
    header: 'Status',
    sortKey: 'status',
    render: (c) => <StatusBadge s={c.status} />,
  },
];

export function CaseTable({
  cases,
  sort,
  onSort,
  footer,
}: {
  cases: Case[];
  sort?: SortState;
  onSort?: (k: SortKey) => void;
  footer?: React.ReactNode;
}) {
  const router = useRouter();
  return (
    <DataTable
      columns={COLUMNS}
      rows={cases}
      rowKey={(c) => c.id}
      onRowClick={(c) => router.push(`/cases/${c.id}`)}
      sort={sort}
      onSort={onSort && ((k) => onSort(k as SortKey))}
      emptyTitle="No cases found"
      emptyHint={<p>Nothing matches this view.</p>}
      footer={footer}
    />
  );
}
