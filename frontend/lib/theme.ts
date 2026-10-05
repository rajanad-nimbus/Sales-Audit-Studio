export const colors = {
  light: {
    bg: '#ffffff',
    bgSecondary: '#f8f9fa',
    bgTertiary: '#f0f2f5',
    fg: '#222d3a',
    fgSecondary: '#626e7c',
    fgTertiary: '#919ba6',
    border: '#dde4ed',
    borderLight: '#e8ecf1',
    primary: '#003b6f',
    primaryHover: '#002d52',
    primaryLight: '#e8eef8',
    accent: '#00a4ef',
    accentHover: '#0088cc',
    success: '#17b26a',
    warning: '#fbb040',
    error: '#e74856',
    info: '#0099cc',
  },
  dark: {
    bg: '#0f1419',
    bgSecondary: '#1a1f29',
    bgTertiary: '#252d38',
    fg: '#e5eaf0',
    fgSecondary: '#a8b3bf',
    fgTertiary: '#7a8390',
    border: '#3d4556',
    borderLight: '#2d3541',
    primary: '#5da3f0',
    primaryHover: '#7ab3f7',
    primaryLight: '#1a2d4a',
    accent: '#40b3ff',
    accentHover: '#66c2ff',
    success: '#5bd986',
    warning: '#fcc965',
    error: '#ff6b7a',
    info: '#4bb8ff',
  },
};

export const typography = {
  fontFamily: {
    primary: '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", "Roboto", "Oxygen", "Ubuntu", "Cantarell", sans-serif',
    mono: '"Monaco", "Menlo", "Ubuntu Mono", monospace',
  },
  fontSize: {
    xs: '0.75rem',
    sm: '0.875rem',
    base: '1rem',
    lg: '1.125rem',
    xl: '1.25rem',
    '2xl': '1.5rem',
    '3xl': '1.875rem',
    '4xl': '2.25rem',
  },
  fontWeight: {
    light: 300,
    normal: 400,
    medium: 500,
    semibold: 600,
    bold: 700,
  },
};

export const spacing = {
  xs: '0.25rem',
  sm: '0.5rem',
  md: '1rem',
  lg: '1.5rem',
  xl: '2rem',
  '2xl': '2.5rem',
  '3xl': '3rem',
};

export const shadows = {
  light: {
    sm: '0 1px 2px 0 rgba(0, 0, 0, 0.05)',
    md: '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)',
    lg: '0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05)',
    xl: '0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04)',
  },
  dark: {
    sm: '0 1px 2px 0 rgba(0, 0, 0, 0.3)',
    md: '0 4px 6px -1px rgba(0, 0, 0, 0.4), 0 2px 4px -1px rgba(0, 0, 0, 0.3)',
    lg: '0 10px 15px -3px rgba(0, 0, 0, 0.5), 0 4px 6px -2px rgba(0, 0, 0, 0.4)',
    xl: '0 20px 25px -5px rgba(0, 0, 0, 0.6), 0 10px 10px -5px rgba(0, 0, 0, 0.5)',
  },
};

export const borderRadius = {
  sm: '0.25rem',
  md: '0.375rem',
  lg: '0.5rem',
  xl: '0.75rem',
};

export type Theme = 'light' | 'dark' | 'system';
