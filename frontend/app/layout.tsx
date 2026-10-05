import type { Metadata } from "next";
import { ThemeProvider } from "@/lib/ThemeContext";
import { ThemeToggle } from "@/components/ThemeToggle";
import "./globals.css";

export const metadata: Metadata = {
  title: "ZeTSA - FastAPI + Next.js",
  description: "Modern full-stack application with FastAPI backend and Next.js frontend",
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
          <nav className="navbar">
            <div className="navbar-container">
              <h1 className="navbar-title">ZeTSA</h1>
              <div className="navbar-actions">
                <ThemeToggle />
              </div>
            </div>
          </nav>
          <main className="main-content">
            <div className="container">
              {children}
            </div>
          </main>
        </ThemeProvider>
      </body>
    </html>
  );
}
