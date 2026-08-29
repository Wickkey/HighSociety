// Shared by every navigation-away action that might abandon a room in
// progress (AppShell's sidebar/title, the Room screen's own back actions):
// confirm first if `hasActiveRoom`, then tear down room state. Ported from
// the old frontend's lobby.js confirmLeaveMidGameIfNeeded/navigateFromSidebar.
import { useCallback } from 'react';
import { useConfirm } from '../state/ConfirmDialogContext';
import { useRoom } from '../state/RoomContext';

/** Returns a function that resolves to whether navigation should proceed --
 * false only when a confirm was shown and declined. On true, room state has
 * already been cleared and it's safe to navigate. */
export function useLeaveRoomGuard(): () => Promise<boolean> {
  const { hasActiveRoom, leaveRoom } = useRoom();
  const confirm = useConfirm();

  return useCallback(async (): Promise<boolean> => {
    if (hasActiveRoom) {
      const ok = await confirm('Leave the game? You can rejoin later.', 'Leave');
      if (!ok) return false;
    }
    leaveRoom();
    return true;
  }, [hasActiveRoom, leaveRoom, confirm]);
}
