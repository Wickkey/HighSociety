// The optional per-move countdown display -- ported from the old frontend's
// gameRenderer.js (startMoveTimer/updateMoveTimerDisplay). The server sends
// one PLAYER_MOVE_TIMER per move with the seconds remaining *at that
// instant* (see gameReducer.ts's PLAYER_MOVE_TIMER case, which turns that
// into `moveDeadline`, a wall-clock ms timestamp); it doesn't tick the
// value down itself, so this hook runs the local countdown from there.
import { useEffect, useState } from 'react';
import { urgentWindowSeconds } from '../state/gameSelectors';

export interface MoveTimerDisplay {
  secondsLeft: number | null;
  isUrgent: boolean;
}

export function useMoveTimer(moveDeadline: number | null, turnTimeLimit: number | null, onExpire: () => void): MoveTimerDisplay {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (moveDeadline === null) return undefined;
    setNow(Date.now()); // don't wait a full tick for the first paint after a fresh deadline
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, [moveDeadline]);

  const remaining = moveDeadline === null ? null : Math.max(0, (moveDeadline - now) / 1000);
  const expired = remaining !== null && remaining <= 0;

  useEffect(() => {
    // `expired` transitions false -> true exactly once per moveDeadline (the
    // ticking effect above keeps re-running this render after that, but the
    // boolean itself doesn't change again), so this fires onExpire exactly
    // once per timeout, not once per 250ms tick.
    if (expired) onExpire();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expired, moveDeadline]);

  if (remaining === null) return { secondsLeft: null, isUrgent: false };
  return {
    secondsLeft: Math.ceil(remaining),
    isUrgent: remaining > 0 && remaining <= urgentWindowSeconds(turnTimeLimit),
  };
}
