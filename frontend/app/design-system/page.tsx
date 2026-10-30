'use client';

import { useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  Drawer,
  EmptyState,
  Field,
  MetricGrid,
  MetricTile,
  Modal,
  PageHeader,
  Segmented,
  SkeletonRows,
  Tabs,
  Toolbar,
} from '@/components/ui';
import type { BadgeVariant } from '@/components/ui';

const SURFACES = ['bg', 'bg-secondary', 'bg-tertiary', 'border', 'sidebar'];
const BRAND = ['navy', 'primary', 'primary-hover', 'primary-light', 'accent'];
const STATUS = ['success', 'warning', 'error', 'info'];
const TEXT = ['fg', 'fg-secondary', 'fg-tertiary', 'primary-text'];
const SIZES = ['xs', 'sm', 'md', 'base', 'lg', 'xl', '2xl', '3xl', 'display'];
const SPACES = ['xs', 'sm', 'md', 'lg', 'xl', '2xl', '3xl'];
const VARIANTS: BadgeVariant[] = ['primary', 'secondary', 'success', 'warning', 'error', 'info'];

function Swatches({ names }: { names: string[] }) {
  return (
    <div className="ds-grid">
      {names.map((n) => (
        <div key={n}>
          <div className="ds-swatch" style={{ background: `var(--${n})` }} />
          <small className="mono">--{n}</small>
        </div>
      ))}
    </div>
  );
}

export default function DesignSystemPage() {
  const [alert, setAlert] = useState(true);
  const [tab, setTab] = useState('open');
  const [seg, setSeg] = useState('all');
  const [overlay, setOverlay] = useState<'modal' | 'drawer' | null>(null);
  const rows = [
    { id: 'C-1042', status: 'Open', exposure: '$1,240.00' },
    { id: 'C-1043', status: 'Resolved', exposure: '$86.50' },
  ];
  return (
    <div className="page-container">
      <PageHeader
        title="Design system"
        subtitle="Tokens and components every screen is built from. Switch the theme to check both modes."
      />

      <Card title="Color: brand">
        <Swatches names={BRAND} />
      </Card>
      <Card title="Color: surfaces">
        <Swatches names={SURFACES} />
      </Card>
      <Card title="Color: text">
        <Swatches names={TEXT} />
      </Card>
      <Card title="Color: status">
        <Swatches names={STATUS} />
      </Card>

      <Card title="Typography">
        <div className="stack stack-sm">
          {SIZES.map((s) => (
            <div key={s} style={{ fontSize: `var(--text-${s})` }}>
              <span className="mono text-faint">--text-{s}</span> Sales audit exceptions
            </div>
          ))}
        </div>
      </Card>

      <Card title="Spacing">
        <div className="stack stack-sm">
          {SPACES.map((s) => (
            <div key={s} className="cluster">
              <span className="mono text-faint" style={{ width: '8rem' }}>
                --space-{s}
              </span>
              <div
                style={{
                  width: `var(--space-${s})`,
                  height: '0.75rem',
                  background: 'var(--primary)',
                  borderRadius: 'var(--radius-sm)',
                }}
              />
            </div>
          ))}
        </div>
      </Card>

      <Card title="Buttons">
        <div className="cluster">
          <Button variant="primary">Primary</Button>
          <Button>Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="danger">Danger</Button>
          <Button variant="primary" disabled>
            Disabled
          </Button>
          <Button variant="primary" size="sm">
            Small
          </Button>
          <Button variant="primary" size="lg">
            Large
          </Button>
        </div>
      </Card>

      <Card title="Badges">
        <div className="cluster">
          {VARIANTS.map((v) => (
            <Badge key={v} variant={v}>
              {v}
            </Badge>
          ))}
        </div>
      </Card>

      <Card title="Alerts">
        <div className="stack stack-sm">
          <Alert tone="info">Informational message.</Alert>
          <Alert tone="success">Saved.</Alert>
          <Alert tone="warning">Heads up: this affects open cases.</Alert>
          {alert && (
            <Alert tone="error" onDismiss={() => setAlert(false)}>
              Something failed. Dismiss me.
            </Alert>
          )}
        </div>
      </Card>

      <Card title="Form fields">
        <div className="form-row">
          <Field label="Store">
            <select defaultValue="">
              <option value="">Select a store</option>
              <option>Store 101</option>
            </select>
          </Field>
          <Field label="Business date" hint="Defaults to the latest closed day.">
            <input type="date" />
          </Field>
          <Field label="Note">
            <input placeholder="Optional" />
          </Field>
        </div>
      </Card>

      <Card title="Table" flush>
        <table className="data-table">
          <thead>
            <tr>
              <th>Case</th>
              <th>Status</th>
              <th className="num">Exposure</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="font-medium">C-1042</td>
              <td>
                <Badge variant="warning" size="sm">
                  Open
                </Badge>
              </td>
              <td className="num">$1,240.00</td>
            </tr>
            <tr>
              <td className="font-medium">C-1043</td>
              <td>
                <Badge variant="success" size="sm">
                  Resolved
                </Badge>
              </td>
              <td className="num">$86.50</td>
            </tr>
          </tbody>
        </table>
      </Card>

      <Card title="Tabs, segmented filter, toolbar">
        <div className="stack">
          <Tabs
            value={tab}
            onChange={setTab}
            items={[
              { id: 'open', label: 'Open', count: 12 },
              { id: 'closed', label: 'Closed', count: 40 },
            ]}
          />
          <Toolbar count="42 results">
            <Segmented
              value={seg}
              onChange={setSeg}
              options={[
                { value: 'all', label: 'All' },
                { value: 'mine', label: 'Mine' },
              ]}
            />
            <input className="grow" placeholder="Search" />
          </Toolbar>
        </div>
      </Card>

      <Card title="Metric tiles">
        <MetricGrid>
          <MetricTile label="Open cases" value="12" />
          <MetricTile label="Exposure" value="$12.4k" onClick={() => undefined} />
        </MetricGrid>
      </Card>

      <Card title="DataTable (rows, loading, empty)">
        <div className="stack">
          <DataTable
            rows={rows}
            rowKey={(r) => r.id}
            columns={[
              { key: 'id', header: 'Case', primary: true, render: (r) => r.id },
              { key: 's', header: 'Status', render: (r) => r.status },
              { key: 'e', header: 'Exposure', numeric: true, render: (r) => r.exposure },
            ]}
          />
          <SkeletonRows rows={2} />
          <DataTable rows={[]} rowKey={() => ''} columns={[]} emptyTitle="No cases" />
        </div>
      </Card>

      <Card title="Modal and drawer">
        <div className="cluster">
          <Button onClick={() => setOverlay('modal')}>Open modal</Button>
          <Button onClick={() => setOverlay('drawer')}>Open drawer</Button>
        </div>
      </Card>
      {overlay === 'modal' && (
        <Modal
          title="Confirm action"
          onClose={() => setOverlay(null)}
          footer={
            <>
              <Button onClick={() => setOverlay(null)}>Cancel</Button>
              <Button variant="primary" onClick={() => setOverlay(null)}>
                Confirm
              </Button>
            </>
          }
        >
          Escape, the close button and a click outside all dismiss this.
        </Modal>
      )}
      {overlay === 'drawer' && (
        <Drawer title="Case C-1042" onClose={() => setOverlay(null)}>
          Detail content goes here.
        </Drawer>
      )}

      <EmptyState title="Nothing here yet">
        Empty states explain what will appear and why.
      </EmptyState>
    </div>
  );
}
