/**
 * TypeScript mirror of the design tokens. The values live in app/styles/tokens.css;
 * this file only names them, so code that needs a color (charts, inline styles) can
 * reference the CSS variable and follow the active light or dark theme.
 */
export type Theme = 'light' | 'dark' | 'system';

const v = (name: string) => `var(--${name})` as const;

export const color = {
  bg: v('bg'),
  surface: v('bg-secondary'),
  surfaceMuted: v('bg-tertiary'),
  text: v('fg'),
  textMuted: v('fg-secondary'),
  textFaint: v('fg-tertiary'),
  border: v('border'),
  borderLight: v('border-light'),
  navy: v('navy'),
  primary: v('primary'),
  primaryHover: v('primary-hover'),
  primaryLight: v('primary-light'),
  primaryText: v('primary-text'),
  accent: v('accent'),
  onPrimary: v('on-primary'),
  success: v('success'),
  warning: v('warning'),
  error: v('error'),
  info: v('info'),
} as const;

export const space = {
  xs: v('space-xs'),
  sm: v('space-sm'),
  md: v('space-md'),
  lg: v('space-lg'),
  xl: v('space-xl'),
  '2xl': v('space-2xl'),
  '3xl': v('space-3xl'),
} as const;

export const radius = {
  sm: v('radius-sm'),
  md: v('radius-md'),
  lg: v('radius-lg'),
  pill: v('radius-pill'),
} as const;

export const shadow = { sm: v('shadow-sm'), md: v('shadow-md'), lg: v('shadow-lg') } as const;

export const text = {
  xs: v('text-xs'),
  sm: v('text-sm'),
  md: v('text-md'),
  base: v('text-base'),
  lg: v('text-lg'),
  xl: v('text-xl'),
  '2xl': v('text-2xl'),
  '3xl': v('text-3xl'),
  display: v('text-display'),
} as const;

/** Semantic status colors, in the order severity usually reads. */
export const statusColor = {
  success: color.success,
  warning: color.warning,
  error: color.error,
  info: color.info,
} as const;
