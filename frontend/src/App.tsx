import { Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from './components/AppShell';
import { Login } from './screens/Login';
import { Home } from './screens/Home';
import { Matchmaking } from './screens/Matchmaking';
import { Room } from './screens/Room/Room';
import { ComingSoon } from './screens/ComingSoon';
import { useProfile } from './state/ProfileContext';

export function App() {
  const { profile } = useProfile();

  if (!profile) {
    // No identity yet -- the whole app is the login screen, no sidebar
    // (matches the old frontend: SIDEBAR_HIDDEN_SCREENS included
    // screen-login, there's nothing to navigate to before you're someone).
    return (
      <div className="content-only">
        <Login />
      </div>
    );
  }

  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<Home />} />
        <Route path="/play" element={<Matchmaking />} />
        <Route path="/host-setup/:panel" element={<Home />} />
        <Route path="/join" element={<Navigate to="/host-setup/join" replace />} />
        <Route path="/host" element={<Navigate to="/host-setup/host" replace />} />
        <Route path="/rules" element={<Navigate to="/host-setup/rules" replace />} />
        <Route path="/leaderboard" element={<ComingSoon title="Leaderboard" />} />
        <Route path="/achievements" element={<ComingSoon title="Achievements" />} />
        <Route path="/account" element={<ComingSoon title="Account" />} />
        <Route path="/my-games" element={<ComingSoon title="My Games" />} />
        <Route path="/room/:code" element={<Room />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
