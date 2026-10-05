'use client';

import { useState } from 'react';
import Link from 'next/link';

interface MenuItem {
  label: string;
  href: string;
  icon: string;
  badge?: number;
  submenu?: MenuItem[];
}

const menuItems: MenuItem[] = [
  {
    label: 'Dashboard',
    href: '/',
    icon: '📊',
  },
  {
    label: 'Users',
    href: '/users',
    icon: '👥',
    badge: 12,
  },
  {
    label: 'Posts',
    href: '/posts',
    icon: '📝',
    badge: 5,
  },
  {
    label: 'Analytics',
    href: '/analytics',
    icon: '📈',
    submenu: [
      {
        label: 'Overview',
        href: '/analytics/overview',
        icon: '👁️',
      },
      {
        label: 'Performance',
        href: '/analytics/performance',
        icon: '⚡',
      },
      {
        label: 'Reports',
        href: '/analytics/reports',
        icon: '📋',
      },
    ],
  },
  {
    label: 'Settings',
    href: '/settings',
    icon: '⚙️',
    submenu: [
      {
        label: 'Profile',
        href: '/settings/profile',
        icon: '👤',
      },
      {
        label: 'Security',
        href: '/settings/security',
        icon: '🔒',
      },
      {
        label: 'Preferences',
        href: '/settings/preferences',
        icon: '🎯',
      },
    ],
  },
];

export function Sidebar() {
  const [isOpen, setIsOpen] = useState(true);
  const [expandedMenu, setExpandedMenu] = useState<string | null>(null);

  const toggleMenu = (label: string) => {
    setExpandedMenu(expandedMenu === label ? null : label);
  };

  return (
    <>
      {/* Sidebar Toggle Button (Mobile) */}
      <button
        className="sidebar-toggle"
        onClick={() => setIsOpen(!isOpen)}
        aria-label="Toggle sidebar"
      >
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <line x1="3" y1="6" x2="21" y2="6" />
          <line x1="3" y1="12" x2="21" y2="12" />
          <line x1="3" y1="18" x2="21" y2="18" />
        </svg>
      </button>

      {/* Sidebar Overlay (Mobile) */}
      {isOpen && <div className="sidebar-overlay" onClick={() => setIsOpen(false)} />}

      {/* Sidebar */}
      <aside className={`sidebar ${isOpen ? 'open' : 'closed'}`}>
        <div className="sidebar-header">
          <h2>Menu</h2>
          <button
            className="sidebar-close"
            onClick={() => setIsOpen(false)}
            aria-label="Close sidebar"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <nav className="sidebar-nav">
          <ul className="menu-list">
            {menuItems.map((item) => (
              <li key={item.href} className="menu-item">
                {item.submenu ? (
                  <>
                    <button
                      className={`menu-link submenu-toggle ${expandedMenu === item.label ? 'expanded' : ''}`}
                      onClick={() => toggleMenu(item.label)}
                    >
                      <span className="menu-icon">{item.icon}</span>
                      <span className="menu-label">{item.label}</span>
                      <svg
                        className="menu-chevron"
                        width="16"
                        height="16"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                      >
                        <polyline points="6 9 12 15 18 9"></polyline>
                      </svg>
                      {item.badge && <span className="menu-badge">{item.badge}</span>}
                    </button>

                    {/* Submenu */}
                    {expandedMenu === item.label && (
                      <ul className="submenu-list">
                        {item.submenu.map((subitem) => (
                          <li key={subitem.href} className="submenu-item">
                            <Link href={subitem.href} className="submenu-link">
                              <span className="submenu-icon">{subitem.icon}</span>
                              <span className="submenu-label">{subitem.label}</span>
                            </Link>
                          </li>
                        ))}
                      </ul>
                    )}
                  </>
                ) : (
                  <Link href={item.href} className="menu-link">
                    <span className="menu-icon">{item.icon}</span>
                    <span className="menu-label">{item.label}</span>
                    {item.badge && <span className="menu-badge">{item.badge}</span>}
                  </Link>
                )}
              </li>
            ))}
          </ul>
        </nav>

        {/* Sidebar Footer */}
        <div className="sidebar-footer">
          <div className="user-card">
            <div className="user-avatar">JD</div>
            <div className="user-info">
              <p className="user-name">John Doe</p>
              <p className="user-email">john@example.com</p>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
