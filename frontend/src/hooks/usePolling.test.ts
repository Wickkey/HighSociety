import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { usePolling } from './usePolling';

describe('usePolling', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('fires immediately, then again every interval', () => {
    const tick = vi.fn();
    renderHook(() => usePolling(tick, 1000));
    expect(tick).toHaveBeenCalledTimes(1); // the "don't wait a full interval for the first render" fix

    vi.advanceTimersByTime(1000);
    expect(tick).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(2000);
    expect(tick).toHaveBeenCalledTimes(4);
  });

  it('never fires when intervalMs is null', () => {
    const tick = vi.fn();
    renderHook(() => usePolling(tick, null));
    vi.advanceTimersByTime(10000);
    expect(tick).not.toHaveBeenCalled();
  });

  it('stops firing once unmounted', () => {
    const tick = vi.fn();
    const { unmount } = renderHook(() => usePolling(tick, 1000));
    unmount();
    vi.advanceTimersByTime(5000);
    expect(tick).toHaveBeenCalledTimes(1); // just the initial call before unmount
  });

  it('picks up a changed callback without needing the caller to memoize it', () => {
    let value = 'first';
    const { rerender } = renderHook(({ v }) => usePolling(() => { value = v; }, 1000), { initialProps: { v: 'first' } });
    rerender({ v: 'second' });
    vi.advanceTimersByTime(1000);
    expect(value).toBe('second');
  });
});
