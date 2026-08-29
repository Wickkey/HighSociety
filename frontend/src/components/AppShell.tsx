// The sidebar + topbar chrome wrapping every screen except Login (see
// App.tsx's route table -- Login renders standalone, nothing else does).
// Ported from index.html's #app-sidebar/.topbar plus profile.js's chip/
// popover.
import { useState } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useProfile } from '../state/ProfileContext';
import styles from './AppShell.module.css';

const SIDEBAR_ITEMS: { to: string; title: string; label: string; iconClassName?: string; icon: React.ReactNode }[] = [
  {
    to: '/play', title: 'Play', label: 'Play',
    icon: <path d="M12 2.65L10.55 3.97C5.4 8.64 2 11.72 2 15.5C2 18.58 4.42 21 7.5 21C9.24 21 10.91 20.19 12 18.91C13.09 20.19 14.76 21 16.5 21C19.58 21 22 18.58 22 15.5C22 11.72 18.6 8.64 13.45 3.96L12 2.65Z" />,
  },
  {
    to: '/join', title: 'Join a Game', label: 'Join',
    icon: (
      <>
        <path d="M10.5 20H6.5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h4" />
        <path d="M15.2 8.3 19 12l-3.8 3.7" />
        <path d="M19 12H9.5" />
      </>
    ),
  },
  {
    to: '/host', title: 'Host a New Game', label: 'Host', iconClassName: styles.sidebarIconHost,
    icon: (
      <>
        <path d="M4 18 L5 10 L8.5 13.5 L12 6.5 L15.5 13.5 L19 10 L20 18 Z" />
        <path d="M6 21h12" />
      </>
    ),
  },
  {
    to: '/leaderboard', title: 'Leaderboard', label: 'Leaderboard',
    icon: (
      <>
        <rect x="3.5" y="14.5" width="4.5" height="6" rx="1.1" />
        <rect x="9.75" y="9.5" width="4.5" height="11" rx="1.1" />
        <rect x="16" y="4.5" width="4.5" height="16" rx="1.1" />
      </>
    ),
  },
  {
    to: '/rules', title: 'How to Play', label: 'How to Play',
    icon: (
      <>
        <path d="M12 7.2c-2.1-1.4-4.9-1.9-8.2-1.6v13c3.3-.3 6.1.2 8.2 1.6 2.1-1.4 4.9-1.9 8.2-1.6v-13c-3.3-.3-6.1.2-8.2 1.6z" />
        <path d="M12 7.2v13" />
      </>
    ),
  },
  {
    to: '/achievements', title: 'Achievements', label: 'Achievements',
    icon: <path d="M12 3.4l2.1 4.4 4.8.6-3.5 3.4.9 4.8L12 14.3l-4.3 2.3.9-4.8-3.5-3.4 4.8-.6L12 3.4z" />,
  },
  {
    to: '/account', title: 'Account', label: 'Account',
    icon: (
      <>
        <circle cx="12" cy="8.3" r="3.6" />
        <path d="M4.8 20c0-3.9 3.3-6.7 7.2-6.7s7.2 2.8 7.2 6.7" />
      </>
    ),
  },
];

function Sidebar() {
  return (
    <nav className={styles.sidebar}>
      {SIDEBAR_ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          title={item.title}
          className={({ isActive }) => `${styles.sidebarItem} ${isActive ? styles.sidebarItemActive : ''}`}
        >
          <svg
            className={`${styles.sidebarIcon} ${item.iconClassName ?? ''}`}
            viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6}
            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
          >
            {item.icon}
          </svg>
          <span>{item.label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

function ProfileChip() {
  const { profile, logout } = useProfile();
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  if (!profile) return null;

  return (
    <div className={styles.profileChipWrap}>
      <button type="button" className={styles.profileChip} onClick={() => setOpen((o) => !o)}>
        <span className={styles.profileChipAvatar}>{profile.username.charAt(0).toUpperCase()}</span>
        {profile.username}
      </button>
      {open && (
        <div className={`card ${styles.popover}`} onMouseLeave={() => setOpen(false)}>
          <button
            type="button"
            className={styles.popoverItem}
            onClick={() => { setOpen(false); navigate('/account'); }}
          >
            Account
          </button>
          <button
            type="button"
            className={`${styles.popoverItem} ${styles.popoverItemDanger}`}
            onClick={() => { setOpen(false); logout(); navigate('/'); }}
          >
            Log out
          </button>
        </div>
      )}
    </div>
  );
}

export function AppShell() {
  const navigate = useNavigate();
  return (
    <div className={styles.shell}>
      <Sidebar />
      <div className={styles.mainCol}>
        <header className={styles.topbar}>
          <button type="button" className={styles.title} onClick={() => navigate('/')}>
            High&nbsp;Society
          </button>
          <ProfileChip />
        </header>
        <div className={styles.content}>
          <Outlet />
        </div>
      </div>
    </div>
  );
}
