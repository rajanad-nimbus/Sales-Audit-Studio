# Theme: navy blue (project rule)

**Rule: navy blue is the brand and accent color across the whole application.** Every new screen, component, chart or state must use the navy tokens below, never a new hard-coded blue, indigo, teal or cyan. Status colors (success green, warning amber, error red) are the only other hues, and only for status.

The style follows the Ontology Studio app: flat surfaces, bordered cards, small muted labels, no gradients or decorative animation. All values live as CSS variables in `frontend/app/globals.css`.

| Token | Light | Dark |
|---|---|---|
| `--navy` (brand mark, top accent) | #0f2347 | #16306a |
| `--primary` (buttons, links, active states) | #17336b | #4a78d0 |
| `--primary-hover` | #0f2550 | #6a92e0 |
| `--primary-light` (selected rows, chips) | #e3e9f6 | navy tint |
| `--accent` / `--info` | #1d4f9e / #2f5fb3 | #6f9bea |
| Sidebar `--sb-bg` (navy in both themes) | #0f2347 | #091428 |

Usage:
- Primary actions, links, focus rings, selected states and progress use `var(--primary)`. Do not write hex blues in components.
- The sidebar is always navy with light text (`--sb-*` tokens). The top bar carries a 3px navy accent line.
- Page and card surfaces are navy-tinted neutrals (`--bg`, `--bg-secondary`, `--bg-tertiary`, `--border`).
- Typography: Inter. Rounded corners 0.5 to 0.75rem. Light, dark and system modes are chosen in Settings (user menu).
