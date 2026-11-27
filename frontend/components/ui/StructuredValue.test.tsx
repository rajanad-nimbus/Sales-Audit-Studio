import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { StructuredValue } from './StructuredValue';
import { humanizeKey, summarizeValue } from '@/lib/humanize';

const policyDetail = {
  ontology_context: {
    source: 'ontology-integration',
    ontology: {
      workspaceId: 100,
      approved: true,
      releaseHash: 'a87c6d019698e1d3ec8c2d4fec562261449c10c5615f9512f818d0a3181951f9',
    },
  },
  reasons: [
    'Required evidence incomplete',
    'Approved ontology control references: NSA-APPROVAL-AUTHORITY, NSA-EVIDENCE-REQUIRED',
  ],
};

it('shows a policy payload as labelled fields instead of JSON', () => {
  const { container } = render(<StructuredValue value={policyDetail} raw={false} />);
  expect(screen.getByText('Ontology context')).toBeInTheDocument();
  expect(screen.getByText('Workspace ID')).toBeInTheDocument();
  expect(screen.getByText('Yes')).toBeInTheDocument();
  expect(screen.getByText('NSA-APPROVAL-AUTHORITY')).toHaveClass('sv-chip');
  expect(screen.getByText('Required evidence incomplete')).toBeInTheDocument();
  expect(container.textContent).not.toContain('{');
  expect(screen.getByTitle(policyDetail.ontology_context.ontology.releaseHash)).toBeInTheDocument();
});

it('lists only the fields that changed between before and after', () => {
  render(
    <StructuredValue
      value={{ before: { status: 'Open', amount: 10 }, after: { status: 'Closed', amount: 10 } }}
      raw={false}
    />
  );
  expect(screen.getByRole('rowheader', { name: 'Status' })).toBeInTheDocument();
  expect(screen.queryByRole('rowheader', { name: 'Amount' })).not.toBeInTheDocument();
  expect(screen.getByText('Closed')).toBeInTheDocument();
});

it('hides raw JSON unless a screen opts in', () => {
  const { rerender } = render(<StructuredValue value={{ a: 1 }} />);
  expect(screen.queryByText('Show raw data')).not.toBeInTheDocument();
  rerender(<StructuredValue value={{ a: 1 }} raw />);
  expect(screen.getByText('Show raw data')).toBeInTheDocument();
});

it('copies a payload as readable text', async () => {
  const { toPlainText } = await import('@/lib/humanize');
  expect(
    toPlainText({ release_id: 20, ontology: { approved: true }, reasons: ['Evidence missing'] })
  ).toBe('Release ID: 20\nOntology:\n  Approved: Yes\nReasons:\n  - Evidence missing');
});

it('turns machine keys and values into words', () => {
  expect(humanizeKey('release_hash')).toBe('Release hash');
  expect(humanizeKey('workspaceId')).toBe('Workspace ID');
  expect(summarizeValue({ enabled: false, limit: 5 })).toBe('Enabled: No; Limit: 5');
});

it('describes API validation errors in words', async () => {
  const { describeValidationErrors } = await import('@/lib/humanize');
  expect(
    describeValidationErrors([
      { loc: ['body', 'column_mapping'], msg: 'Value error, too many fields', type: 'value_error' },
      { loc: ['body', 'name'], msg: 'Field required' },
    ])
  ).toBe('Column mapping: too many fields; Name: Field required');
});

it('labels codes, actors, datasets and freshness in plain words', async () => {
  const { codeLabel, datasetLabel, freshnessLabel, sourceLabel } = await import('@/lib/humanize');
  const { actorLabel } = await import('@/lib/activityUtil');
  expect(codeLabel('TENDER_NOT_FOUND')).toBe('Tender not found');
  expect(codeLabel('NOSALE_EXCESS')).toBe('No sale excess');
  expect(actorLabel('transaction-audit-engine')).toBe('Transaction Audit Engine');
  expect(actorLabel('analyst.one@example.com')).toBe('analyst.one@example.com');
  expect(datasetLabel('audited_transactions')).toBe('Audited transactions');
  expect(freshnessLabel('never')).toBe('Never received');
  expect(sourceLabel('ManualAdjustment')).toBe('Manual correction');
});

it('shortens UUIDs and labels snake and screaming codes inside payloads', () => {
  render(
    <StructuredValue
      value={{
        trigger: 'previous_day_closed',
        code: 'TENDER_NOT_FOUND',
        event_id: '3f1c2a9e-1b2c-4d5e-8f90-123456789abc',
      }}
    />
  );
  expect(screen.getByText('Previous day closed')).toBeInTheDocument();
  expect(screen.getByText('Tender not found')).toBeInTheDocument();
  expect(screen.getByTitle('3f1c2a9e-1b2c-4d5e-8f90-123456789abc')).toBeInTheDocument();
});

it('describes row errors from file imports', async () => {
  const { describeValidationErrors } = await import('@/lib/humanize');
  expect(describeValidationErrors([{ row: 3, message: 'Unknown category' }])).toBe(
    'Row 3: Unknown category'
  );
});

it('fixes "(s)" plurals and badge casing', async () => {
  const { tidyPlurals, statusLabel, plural } = await import('@/lib/humanize');
  expect(tidyPlurals('2 open case(s), 1 unexplained variance(s)')).toBe(
    '2 open cases, 1 unexplained variance'
  );
  expect(tidyPlurals('1 store-level close marker(s) received')).toBe(
    '1 store-level close marker received'
  );
  expect(tidyPlurals('3 record(s), 2 acknowledged')).toBe('3 records, 2 acknowledged');
  expect(statusLabel('Sales Audit In Progress')).toBe('Sales audit in progress');
  expect(statusLabel('Store Errors Pending')).toBe('Store errors pending');
  expect(statusLabel('No control total')).toBe('No control total');
  expect(statusLabel('POS Day Close Received')).toBe('POS day close received');
  expect(plural(1, 'case')).toBe('1 case');
});

it('labels missing keys as empty instead of throwing', async () => {
  const { humanizeKey } = await import('@/lib/humanize');
  expect(humanizeKey(undefined)).toBe('');
  expect(humanizeKey(null)).toBe('');
});
