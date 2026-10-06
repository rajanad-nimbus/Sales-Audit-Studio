'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Case, money } from '@/lib/api';

const STATUS_TONE: Record<string, string> = {
  Open: 'badge-secondary', 'In Investigation': 'badge-info', 'In Review': 'badge-warning',
  Resolving: 'badge-primary', 'Pending Validation': 'badge-primary', Closed: 'badge-success',
};

export const PriorityBadge = ({ p }: { p: string }) => (
  <span className={`badge badge-sm ${p === 'Critical' || p === 'High' ? 'badge-error' : 'badge-secondary'}`}>{p}</span>
);

export const StatusBadge = ({ s }: { s: string }) => (
  <span className={`badge badge-sm ${STATUS_TONE[s] ?? 'badge-secondary'}`}>{s}</span>
);

export const Evidence = ({ pct }: { pct: number }) => (
  <span className="ev-cell">
    <span className={`ev-bar ${pct >= 100 ? 'full' : ''}`} aria-hidden><i style={{ width: `${Math.min(100, pct)}%` }} /></span>
    {pct}%
  </span>
);

export type SortKey = 'case_number' | 'case_type' | 'store_id' | 'total_exception_amount' | 'total_exposure' | 'evidence_completeness' | 'priority' | 'status';
export interface SortState { key: SortKey; dir: 'asc' | 'desc' }

export function CaseTable({ cases, sort, onSort }: { cases: Case[]; sort?: SortState; onSort?: (k: SortKey) => void }) {
  const router = useRouter();
  if (cases.length === 0) return <div className="empty-state"><b>No cases found</b><p>Nothing matches this view.</p></div>;
  return (
    <div className="table-container">
      <table className="data-table">
        <thead>
          <tr>
            {([['case_number', 'Case'], ['case_type', 'Type'], ['store_id', 'Store'], ['total_exception_amount', 'Exception', 1], ['total_exposure', 'Exposure', 1],
              ['evidence_completeness', 'Evidence', 1], ['priority', 'Priority'], ['status', 'Status']] as [SortKey, string, number?][]).map(([k, label, num]) => (
              <th key={k} className={num ? 'num' : ''} aria-sort={sort?.key === k ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                {onSort ? <button className="ac-th" onClick={() => onSort(k)}>{label}{sort?.key === k ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : ''}</button> : label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cases.map((c) => (
            <tr key={c.id} className="row-link" tabIndex={0} onClick={() => router.push(`/cases/${c.id}`)}
              onKeyDown={(e) => { if (e.key === 'Enter') router.push(`/cases/${c.id}`); }}>
              <td className="font-medium"><Link href={`/cases/${c.id}`} onClick={(e) => e.stopPropagation()}>{c.case_number}</Link></td>
              <td>{c.case_type}</td>
              <td>{c.store_id}</td>
              <td className="num">{money(c.total_exception_amount)}</td>
              <td className="num">{money(c.total_exposure)}</td>
              <td className="num"><Evidence pct={c.evidence_completeness} /></td>
              <td><PriorityBadge p={c.priority} /></td>
              <td><StatusBadge s={c.status} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
