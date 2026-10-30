# Design system

Flat surfaces, bordered cards, **navy as the only brand/accent hue**, light and dark modes. Live reference: `/design-system`.

## Layers

| Layer             | Where                   | Rule                                                                                                                                                                                           |
| ----------------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tokens            | `app/styles/tokens.css` | Only place raw colors, sizes, radii, shadows are defined. Light + dark.                                                                                                                        |
| Token names in TS | `lib/theme.ts`          | `var(--x)` references for charts and inline styles.                                                                                                                                            |
| Shared classes    | `app/globals.css`       | Built only from tokens. No new raw hex or `rgba`.                                                                                                                                              |
| Components        | `components/ui/`        | `Button`, `Badge`, `Alert`, `Card`, `Field`, `PageHeader`, `EmptyState`, `DataTable`, `Tabs`, `Segmented`, `Toolbar`, `MetricTile`/`MetricGrid`, `Skeleton`/`SkeletonRows`, `Modal`, `Drawer`. |

## Rules

1. Use a primitive from `components/ui` before writing markup with classes by hand.
2. Colors come from tokens. Brand/interactive = `--primary`, `--primary-text`, `--primary-light`, `--navy`. Status = `--success`, `--warning`, `--error`, `--info`, used for meaning only, never decoration.
3. Text on a navy or primary fill is `--on-primary`.
4. Spacing uses `--space-*`; radius `--radius-sm|md|lg|pill`; type `--text-*`; shadows `--shadow-*`; layers `--z-*`.
5. Every token that changes between themes is defined in both dark blocks of `tokens.css` (media query and `[data-theme='dark']`). Keep them in sync.
6. Check any new screen in light and dark.

## Enforcement

`npm run check:design` fails on raw colors (hex, `rgb()`, `hsl()`) outside `tokens.css` and on hand-built `className="alert …"` markup. `Logo.tsx` is exempt as the brand mark.

## Tokens

- **Surfaces:** `--bg`, `--bg-secondary` (cards), `--bg-tertiary` (table heads, hover), `--border`, `--border-light`, `--sidebar`.
- **Text:** `--fg`, `--fg-secondary`, `--fg-tertiary`.
- **Brand:** `--navy`, `--primary`, `--primary-hover`, `--primary-light`, `--primary-text`, `--accent`.
- **Status:** `--success`, `--warning`, `--error`, `--info`. Tints: `color-mix(in oklab, var(--x) 12%, transparent)`.
- **Type:** `--font-sans`, `--font-mono`, `--text-xs` to `--text-display`, `--weight-*`, `--leading-*`.
- **Shape and depth:** `--radius-*`, `--shadow-*`, `--scrim`, `--focus-ring`.
- **Motion:** `--duration-fast|base`, `--ease`.

## Components

```tsx
import { Alert, Badge, Button, Card, EmptyState, Field, PageHeader } from '@/components/ui';

<PageHeader title="Cases" subtitle="Sorted by exposure" actions={<Button variant="primary">New</Button>} />
<Card title="Filters"><Field label="Store"><select /></Field></Card>
<Badge variant="warning" size="sm">Open</Badge>
<Alert tone="error" onDismiss={clear}>Failed</Alert>
```

```tsx
<DataTable
  rows={cases}
  rowKey={(c) => c.id}
  onRowClick={open}
  columns={[
    { key: 'id', header: 'Case', primary: true, render: (c) => c.id },
    { key: 'x', header: 'Exposure', numeric: true, render: (c) => money(c.x) },
  ]}
/>
```

`rows={null}` shows a loading skeleton; an empty array shows the empty state. Toasts stay `useToast()` from `components/Feedback`.

## Adopting in existing screens

Existing pages still use the class names directly, which is fine: the classes and the primitives share one implementation. Migrate a page to the primitives when you next touch it. Raw colors in `globals.css` have been moved to tokens. Most tables use `DataTable`. The rest (merged cells, in-table forms, grouped rows) use the `data-table` class directly.
