import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import { ThemeProvider } from '@/lib/ThemeContext';
import { AppShell } from '@/components/AppShell';
import { ToastProvider } from '@/components/Feedback';
import { MeProvider } from '@/components/MeProvider';
import './styles/tokens.css';
import './globals.css';

// Served from this app (no request to Google at runtime) and exposed to the tokens as --font-inter.
const inter = Inter({ subsets: ['latin'], variable: '--font-inter', display: 'swap' });

export const metadata: Metadata = {
  title: 'Sales Audit Studio',
  description: 'Agentic financial exception management for retail commerce',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable} suppressHydrationWarning>
      <body>
        <ThemeProvider>
          <ToastProvider>
            <MeProvider>
              <AppShell>{children}</AppShell>
            </MeProvider>
          </ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
