import { NavLink, Outlet } from 'react-router-dom'
import './Layout.css'

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/forecasting', label: 'Forecasting', end: false },
  { to: '/anomalies', label: 'Anomalies', end: false },
]

// Separate demo feature -- scores arbitrary external company data, not the
// loaded BDG2 portfolio the items above analyze -- so it's visually split
// out rather than mixed into the main analytics nav.
const DEMO_NAV_ITEMS = [{ to: '/external-forecast', label: 'External Forecast', end: false }]

export function Layout() {
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <span className="sidebar-brand-mark" aria-hidden="true" />
          <span className="sidebar-brand-text">Energy Intelligence</span>
        </div>
        <nav className="sidebar-nav" aria-label="Main navigation">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => 'sidebar-link' + (isActive ? ' sidebar-link-active' : '')}
            >
              {item.label}
            </NavLink>
          ))}
          <div className="sidebar-nav-divider" role="separator" />
          {DEMO_NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => 'sidebar-link' + (isActive ? ' sidebar-link-active' : '')}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span>BDG2 subset &middot; 3 sites &middot; 60 buildings</span>
        </div>
      </aside>
      <div className="layout-main">
        <Outlet />
      </div>
    </div>
  )
}
