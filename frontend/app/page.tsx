"use client";

import { useEffect, useState } from "react";
import axios from "axios";

interface HealthStatus {
  status: string;
}

interface ApiStats {
  version: string;
  environment: string;
  timestamp: string;
}

export default function Home() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [stats, setStats] = useState<ApiStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [healthRes] = await Promise.all([
          axios.get(`${process.env.NEXT_PUBLIC_API_URL}/api/health`),
        ]);
        setHealth(healthRes.data);
        setStats({
          version: "1.0.0",
          environment: "development",
          timestamp: new Date().toISOString(),
        });
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to connect to backend"
        );
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  return (
    <div className="home-page">
      {/* Hero Section */}
      <section className="hero-section">
        <div className="hero-content">
          <h1>Welcome to ZeTSA</h1>
          <p className="hero-subtitle">
            A modern full-stack application with FastAPI, PostgreSQL, and Next.js
          </p>
          <div className="hero-buttons">
            <button className="btn-primary btn-lg">Get Started</button>
            <button className="btn-secondary btn-lg">Learn More</button>
          </div>
        </div>
      </section>

      {/* Status Section */}
      <section className="status-section">
        <h2>System Status</h2>
        <div className="status-grid">
          {/* Backend Health */}
          <div className="status-card">
            <div className="card-header">
              <h3>Backend API</h3>
              <span className={`status-badge ${loading ? "pending" : health ? "healthy" : "unhealthy"}`}>
                {loading ? "Checking..." : health ? "Healthy" : "Unhealthy"}
              </span>
            </div>
            <div className="card-content">
              {loading && <p className="text-secondary">Connecting to backend...</p>}
              {error && <p className="text-error">{error}</p>}
              {health && (
                <div className="status-details">
                  <div className="status-item">
                    <span className="label">Status</span>
                    <span className="value text-success">{health.status}</span>
                  </div>
                  {stats && (
                    <>
                      <div className="status-item">
                        <span className="label">Version</span>
                        <span className="value">{stats.version}</span>
                      </div>
                      <div className="status-item">
                        <span className="label">Environment</span>
                        <span className="value">{stats.environment}</span>
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Database Status */}
          <div className="status-card">
            <div className="card-header">
              <h3>Database</h3>
              <span className={`status-badge ${health ? "healthy" : "unknown"}`}>
                {health ? "Connected" : "Unknown"}
              </span>
            </div>
            <div className="card-content">
              <div className="status-details">
                <div className="status-item">
                  <span className="label">Type</span>
                  <span className="value">PostgreSQL 18</span>
                </div>
                <div className="status-item">
                  <span className="label">Host</span>
                  <span className="value">postgres:5432</span>
                </div>
                <div className="status-item">
                  <span className="label">Connection</span>
                  <span className={`value ${health ? "text-success" : "text-warning"}`}>
                    {health ? "Active" : "Pending"}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Frontend Info */}
          <div className="status-card">
            <div className="card-header">
              <h3>Frontend</h3>
              <span className="status-badge healthy">Running</span>
            </div>
            <div className="card-content">
              <div className="status-details">
                <div className="status-item">
                  <span className="label">Framework</span>
                  <span className="value">Next.js 16</span>
                </div>
                <div className="status-item">
                  <span className="label">Runtime</span>
                  <span className="value">Node.js 22</span>
                </div>
                <div className="status-item">
                  <span className="label">Status</span>
                  <span className="value text-success">Ready</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section className="features-section">
        <h2>Features</h2>
        <div className="features-grid">
          <div className="feature-card">
            <div className="feature-icon">🚀</div>
            <h3>Fast & Modern</h3>
            <p>Built with the latest technologies for optimal performance and developer experience</p>
          </div>
          <div className="feature-card">
            <div className="feature-icon">🎨</div>
            <h3>Beautiful Design</h3>
            <p>HubSpot-inspired design with support for light and dark modes</p>
          </div>
          <div className="feature-card">
            <div className="feature-icon">🔐</div>
            <h3>Type Safe</h3>
            <p>Full TypeScript support with comprehensive type safety across the stack</p>
          </div>
          <div className="feature-card">
            <div className="feature-icon">📦</div>
            <h3>Containerized</h3>
            <p>Docker support for easy deployment and consistent development environments</p>
          </div>
          <div className="feature-card">
            <div className="feature-icon">🗄️</div>
            <h3>Database Ready</h3>
            <p>PostgreSQL with SQLAlchemy ORM for robust data management</p>
          </div>
          <div className="feature-card">
            <div className="feature-icon">🌙</div>
            <h3>Dark Mode</h3>
            <p>Seamless switching between light and dark themes with system preference support</p>
          </div>
        </div>
      </section>

      {/* Quick Start Section */}
      <section className="quickstart-section">
        <h2>Quick Start</h2>
        <div className="quickstart-content">
          <div className="quickstart-step">
            <div className="step-number">1</div>
            <h3>Start Backend</h3>
            <pre><code>cd backend && python -m venv venv && source venv/bin/activate && pip install -r requirements.txt && uvicorn main:app --reload</code></pre>
          </div>
          <div className="quickstart-step">
            <div className="step-number">2</div>
            <h3>Start Frontend</h3>
            <pre><code>cd frontend && npm install && npm run dev</code></pre>
          </div>
          <div className="quickstart-step">
            <div className="step-number">3</div>
            <h3>Start Database</h3>
            <pre><code>docker compose up -d</code></pre>
          </div>
        </div>
      </section>
    </div>
  );
}
