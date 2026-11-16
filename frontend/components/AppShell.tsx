'use client';

import { usePathname } from 'next/navigation';
import { Sidebar } from '@/components/Sidebar';
import { ChatPanel, ChatProvider } from '@/components/Chat';
import { AccessGuard } from '@/components/MeProvider';
import { TopBar } from '@/components/TopBar';

/** The app frame (sidebar, top bar, assistant). The sign-in screen is shown bare, without it. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  if (pathname === '/login') return <>{children}</>;
  return (
    <ChatProvider>
      <div className="app-layout">
        <a className="skip-link" href="#main-content">
          Skip to workspace
        </a>
        <Sidebar />
        <div className="app-body">
          <TopBar />
          <main className="main-content" id="main-content" tabIndex={-1}>
            <div className="container">
              <AccessGuard>{children}</AccessGuard>
            </div>
          </main>
        </div>
        <ChatPanel />
      </div>
    </ChatProvider>
  );
}
