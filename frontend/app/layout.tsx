import type { Metadata } from 'next';
import { ThemeProvider } from '@/lib/ThemeContext';
import { AppShell } from '@/components/AppShell';
import { ToastProvider } from '@/components/Feedback';
import { MeProvider } from '@/components/MeProvider';
import './globals.css';

export const metadata: Metadata = {
  title: 'Sales Audit Studio',
  description: 'Agentic financial exception management for retail commerce',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
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
