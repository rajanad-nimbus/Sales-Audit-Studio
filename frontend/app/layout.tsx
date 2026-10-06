import type { Metadata } from "next";
import { ThemeProvider } from "@/lib/ThemeContext";
import { Sidebar } from "@/components/Sidebar";
import { ChatPanel, ChatProvider } from "@/components/Chat";
import { ToastProvider } from "@/components/Feedback";
import { AccessGuard, MeProvider } from "@/components/MeProvider";
import { TopBar } from "@/components/TopBar";
import "./globals.css";

export const metadata: Metadata = {
  title: "Nimbus Sales Audit",
  description: "Agentic financial exception management for retail commerce",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <ThemeProvider>
          <ToastProvider>
          <MeProvider>
          <ChatProvider>
          <div className="app-layout">
            <Sidebar />
            <div className="app-body">
              <TopBar />
              <main className="main-content">
                <div className="container">
                  <AccessGuard>{children}</AccessGuard>
                </div>
              </main>
            </div>
            <ChatPanel />
          </div>
          </ChatProvider>
          </MeProvider>
          </ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
