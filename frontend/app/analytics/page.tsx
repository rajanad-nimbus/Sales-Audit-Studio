'use client';

import { useEffect, useState } from 'react';
import axios from 'axios';

interface Stats {
  users: number;
  posts: number;
  avg_posts_per_user: number;
  latest_user: string;
  latest_post: string;
}

export default function AnalyticsPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchStats = async () => {
      try {
        setLoading(true);
        const [usersRes, postsRes] = await Promise.all([
          axios.get(`${process.env.NEXT_PUBLIC_API_URL}/api/users/count`),
          axios.get(`${process.env.NEXT_PUBLIC_API_URL}/api/posts/count`),
        ]);

        const userCount = usersRes.data.count || 0;
        const postCount = postsRes.data.count || 0;

        setStats({
          users: userCount,
          posts: postCount,
          avg_posts_per_user: userCount > 0 ? (postCount / userCount).toFixed(2) as any : 0,
          latest_user: new Date().toLocaleDateString(),
          latest_post: new Date().toLocaleDateString(),
        });
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch analytics');
      } finally {
        setLoading(false);
      }
    };

    fetchStats();
  }, []);

  if (loading) {
    return (
      <div className="page-container">
        <h1>Analytics</h1>
        <div className="loading">Loading analytics...</div>
      </div>
    );
  }

  return (
    <div className="page-container">
      <h1>Analytics Overview</h1>

      {error && <div className="alert alert-error">{error}</div>}

      {stats && (
        <>
          {/* KPI Cards */}
          <div className="kpi-grid">
            <div className="kpi-card">
              <div className="kpi-header">
                <h3>Total Users</h3>
                <span className="kpi-icon">👥</span>
              </div>
              <div className="kpi-value">{stats.users}</div>
              <p className="kpi-label">Active users in system</p>
            </div>

            <div className="kpi-card">
              <div className="kpi-header">
                <h3>Total Posts</h3>
                <span className="kpi-icon">📝</span>
              </div>
              <div className="kpi-value">{stats.posts}</div>
              <p className="kpi-label">Published posts</p>
            </div>

            <div className="kpi-card">
              <div className="kpi-header">
                <h3>Avg Posts/User</h3>
                <span className="kpi-icon">📊</span>
              </div>
              <div className="kpi-value">{stats.avg_posts_per_user}</div>
              <p className="kpi-label">Average engagement</p>
            </div>

            <div className="kpi-card">
              <div className="kpi-header">
                <h3>Engagement Rate</h3>
                <span className="kpi-icon">⚡</span>
              </div>
              <div className="kpi-value">
                {stats.users > 0 ? ((stats.posts / stats.users * 100).toFixed(1)) : 0}%
              </div>
              <p className="kpi-label">Posts per user ratio</p>
            </div>
          </div>

          {/* Activity Details */}
          <div className="activity-section">
            <h2>Activity Summary</h2>
            <div className="activity-grid">
              <div className="activity-card">
                <h4>User Metrics</h4>
                <ul className="metric-list">
                  <li>
                    <span>Total Users</span>
                    <strong>{stats.users}</strong>
                  </li>
                  <li>
                    <span>Users per post</span>
                    <strong>{stats.users > 0 ? stats.posts : 0}</strong>
                  </li>
                </ul>
              </div>

              <div className="activity-card">
                <h4>Content Metrics</h4>
                <ul className="metric-list">
                  <li>
                    <span>Total Posts</span>
                    <strong>{stats.posts}</strong>
                  </li>
                  <li>
                    <span>Avg per user</span>
                    <strong>{stats.avg_posts_per_user}</strong>
                  </li>
                </ul>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
