'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Alert, Button, DataTable, PageHeader, Segmented, Toolbar } from '@/components/ui';
import { apiError } from '@/lib/storeDays';

interface Report {
  title: string;
  columns: string[];
  rows: (string | number | null)[][];
  truncated: boolean;
}

const REPORTS = [
  { value: 'variances', label: 'Total variances' },
  { value: 'errors', label: 'Errors by store' },
  { value: 'store-days', label: 'Store day status' },
  { value: 'tax', label: 'Tax / VAT' },
];

export default function ReportsPage() {
  const [name, setName] = useState('variances');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const params = { start: start || undefined, end: end || undefined };

  const load = useCallback(async () => {
    setReport(null);
    try {
      const { data } = await api.get<Report>(`/api/reports/${name}`, {
        params: { start: start || undefined, end: end || undefined },
      });
      setReport(data);
      setError(null);
    } catch (e) {
      setError(apiError(e, 'Could not run the report.'));
    }
  }, [name, start, end]);
  useEffect(() => {
    load();
  }, [load]);

  const download = async () => {
    try {
      const { data } = await api.get(`/api/reports/${name}`, {
        params: { ...params, format: 'csv' },
        responseType: 'blob',
      });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(data);
      link.download = `${name}_${start || 'start'}_${end || 'now'}.csv`;
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (e) {
      setError(apiError(e, 'Could not download the report.'));
    }
  };

  return (
    <div className="page-container">
      <PageHeader
        title="Reports"
        subtitle="Standard sales-audit reports for the stores you can see. Each can be downloaded as CSV."
        actions={
          <Button onClick={() => void download()} disabled={!report || report.rows.length === 0}>
            Download CSV
          </Button>
        }
      />
      {error && <Alert tone="error">{error}</Alert>}
      <Toolbar
        count={
          report
            ? `${report.rows.length} row${report.rows.length === 1 ? '' : 's'}${report.truncated ? ' (first 5,000)' : ''}`
            : undefined
        }
      >
        <Segmented value={name} onChange={setName} options={REPORTS} />
        <input
          type="date"
          aria-label="From"
          value={start}
          max={end || undefined}
          onChange={(e) => setStart(e.target.value)}
        />
        <input
          type="date"
          aria-label="To"
          value={end}
          min={start || undefined}
          onChange={(e) => setEnd(e.target.value)}
        />
      </Toolbar>
      <DataTable
        columns={(report?.columns ?? []).map((header, i) => ({
          key: header,
          header,
          primary: i === 0,
          render: (r: (string | number | null)[]) => r[i] ?? '',
        }))}
        rows={report ? report.rows : null}
        rowKey={(r) => r.join('|')}
        emptyTitle="Nothing to report for this range"
      />
    </div>
  );
}
