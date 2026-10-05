'use client';

export default function AnalyticsOverviewPage() {
  return (
    <div className="page-container">
      <div className="page-header">
        <h1>Analytics Overview</h1>
      </div>

      <div className="info-card">
        <h2>System Health</h2>
        <div className="health-indicators">
          <div className="indicator">
            <span className="status healthy"></span>
            <div>
              <h4>Backend API</h4>
              <p>Running normally</p>
            </div>
          </div>
          <div className="indicator">
            <span className="status healthy"></span>
            <div>
              <h4>Database</h4>
              <p>Connection stable</p>
            </div>
          </div>
          <div className="indicator">
            <span className="status healthy"></span>
            <div>
              <h4>Frontend</h4>
              <p>Operating normally</p>
            </div>
          </div>
        </div>
      </div>

      <div className="info-card">
        <h2>Quick Stats</h2>
        <p>Navigate to the main Analytics page to view detailed statistics about users and posts.</p>
      </div>
    </div>
  );
}
