# ZeTSA Design System & Theme

## Overview

ZeTSA uses a comprehensive design system built on HubSpot's design principles with navy blue (#003b6f) as the primary brand color. The application supports both light and dark modes with seamless switching.

## Color Palette

### Light Mode
```
Primary:        #003b6f (Navy Blue)
Primary Hover:  #002d52 (Darker Navy)
Primary Light:  #e8eef8 (Light Navy background)
Accent:         #00a4ef (Bright Blue)
Success:        #17b26a (Green)
Warning:        #fbb040 (Amber)
Error:          #e74856 (Red)
Info:           #0099cc (Cyan)

Backgrounds:
- Main:         #ffffff (White)
- Secondary:    #f8f9fa (Light Gray)
- Tertiary:     #f0f2f5 (Medium Gray)

Text:
- Primary:      #222d3a (Dark Gray)
- Secondary:    #626e7c (Medium Gray)
- Tertiary:     #919ba6 (Light Gray)

Borders:
- Standard:     #dde4ed (Light Border)
- Light:        #e8ecf1 (Lighter Border)
```

### Dark Mode
```
Primary:        #5da3f0 (Bright Navy)
Primary Hover:  #7ab3f7 (Lighter Navy)
Primary Light:  #1a2d4a (Dark Navy background)
Accent:         #40b3ff (Bright Cyan)
Success:        #5bd986 (Bright Green)
Warning:        #fcc965 (Bright Amber)
Error:          #ff6b7a (Bright Red)
Info:           #4bb8ff (Bright Cyan)

Backgrounds:
- Main:         #0f1419 (Very Dark)
- Secondary:    #1a1f29 (Dark)
- Tertiary:     #252d38 (Medium Dark)

Text:
- Primary:      #e5eaf0 (Light Gray)
- Secondary:    #a8b3bf (Medium Gray)
- Tertiary:     #7a8390 (Darker Gray)

Borders:
- Standard:     #3d4556 (Dark Border)
- Light:        #2d3541 (Lighter Dark Border)
```

## CSS Custom Properties

All colors are defined as CSS custom properties (variables) in `:root`:

```css
/* Colors */
--bg, --bg-secondary, --bg-tertiary
--fg, --fg-secondary, --fg-tertiary
--border, --border-light
--primary, --primary-hover, --primary-light
--accent, --accent-hover
--success, --warning, --error, --info

/* Spacing */
--space-xs, --space-sm, --space-md, --space-lg, --space-xl, --space-2xl, --space-3xl

/* Shadows */
--shadow-sm, --shadow-md, --shadow-lg
```

## Typography

### Font Family
- **Primary**: Inter (Google Fonts)
- **Fallback**: System fonts (-apple-system, BlinkMacSystemFont, Segoe UI, etc.)
- **Monospace**: Monaco, Menlo, Ubuntu Mono

### Font Sizes
```
xs:    0.75rem   (12px)
sm:    0.875rem  (14px)
base:  1rem      (16px)
lg:    1.125rem  (18px)
xl:    1.25rem   (20px)
2xl:   1.5rem    (24px)
3xl:   1.875rem  (30px)
4xl:   2.25rem   (36px)
```

### Font Weights
```
light:     300
normal:    400
medium:    500
semibold:  600
bold:      700
```

## Components

### Buttons
```html
<!-- Primary Button -->
<button class="btn-primary">Primary</button>

<!-- Secondary Button -->
<button class="btn-secondary">Secondary</button>

<!-- Accent Button -->
<button class="btn-accent">Accent</button>

<!-- Sizes -->
<button class="btn-primary btn-sm">Small</button>
<button class="btn-primary">Medium</button>
<button class="btn-primary btn-lg">Large</button>
```

### Badges
```html
<span class="badge badge-primary">Primary</span>
<span class="badge badge-success">Success</span>
<span class="badge badge-warning">Warning</span>
<span class="badge badge-error">Error</span>
<span class="badge badge-info">Info</span>

<!-- Sizes -->
<span class="badge badge-sm">Small</span>
<span class="badge badge-md">Medium</span>
<span class="badge badge-lg">Large</span>
```

### Cards
```html
<div class="card">
  <h3>Card Title</h3>
  <p>Card content goes here</p>
</div>
```

### Theme Toggle
```tsx
import { ThemeToggle } from '@/components/ThemeToggle';

export default function MyComponent() {
  return <ThemeToggle />;
}
```

## React Hooks & Context

### useTheme Hook
Access and modify the current theme in any component:

```tsx
import { useTheme } from '@/lib/ThemeContext';

export default function MyComponent() {
  const { theme, setTheme } = useTheme();

  return (
    <div>
      <p>Current theme: {theme}</p>
      <button onClick={() => setTheme('dark')}>Dark Mode</button>
      <button onClick={() => setTheme('light')}>Light Mode</button>
      <button onClick={() => setTheme('system')}>System</button>
    </div>
  );
}
```

### Theme Values
- `'light'` - Light mode forced
- `'dark'` - Dark mode forced
- `'system'` - Use system preference (default)

## Creating New Components

When creating components, always use CSS custom properties for colors:

```css
.my-component {
  background-color: var(--bg-secondary);
  color: var(--fg);
  border: 1px solid var(--border);
}

.my-component:hover {
  background-color: var(--bg-tertiary);
  border-color: var(--primary);
}
```

Never hardcode colors—always use the theme tokens.

## Responsive Design

The design system is mobile-first with breakpoints:

```css
/* Mobile: default */
.my-component {
  grid-template-columns: 1fr;
}

/* Tablet & Desktop */
@media (min-width: 768px) {
  .my-component {
    grid-template-columns: repeat(2, 1fr);
  }
}
```

## Accessibility

- All interactive elements have visible focus states
- Colors meet WCAG AA contrast ratios in both light and dark modes
- Reduced motion preferences are respected
- Semantic HTML is used throughout
- ARIA labels on icon-only buttons

## Using the Theme in TypeScript

Import the theme types and values:

```typescript
import { colors, typography, spacing, shadows } from '@/lib/theme';
import type { Theme } from '@/lib/theme';

const primaryColor = colors.light.primary; // #003b6f
```

## Light/Dark Mode Implementation

The theme switching works through multiple layers:

1. **CSS Custom Properties**: Primary layer, defined at `:root`
2. **prefers-color-scheme Media Query**: Respects system preference
3. **data-theme Attribute**: Allows explicit override
4. **localStorage**: Persists user's theme choice

```css
:root {
  /* Light theme (default) */
  --primary: #003b6f;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    /* Dark theme when system prefers dark and no explicit light choice */
    --primary: #5da3f0;
    color-scheme: dark;
  }
}

:root[data-theme="dark"] {
  /* Dark theme when explicitly set */
  --primary: #5da3f0;
  color-scheme: dark;
}

:root[data-theme="light"] {
  /* Light theme when explicitly set */
  --primary: #003b6f;
  color-scheme: light;
}
```

## Testing Themes

### Manually Switch Themes
1. Click the theme toggle in the navbar (top right)
2. Choose Light, Dark, or System

### Check System Preference
- **macOS**: System Preferences → General → Appearance
- **Windows**: Settings → Personalization → Colors
- **Linux**: Depends on desktop environment

### Browser DevTools
Simulate prefers-color-scheme in Chrome DevTools:
1. Open DevTools
2. Command + Shift + P (or Ctrl + Shift + P on Windows)
3. Search for "Emulate CSS media" 
4. Select `prefers-color-scheme: dark` or `prefers-color-scheme: light`

## Future Enhancements

- [ ] Additional color schemes (e.g., high contrast mode)
- [ ] Custom theme builder
- [ ] Theme export/import functionality
- [ ] Component variants and states
- [ ] Storybook integration
- [ ] Theme analytics
