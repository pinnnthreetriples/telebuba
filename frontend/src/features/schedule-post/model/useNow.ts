import { useEffect, useState } from 'react';

// A clock that re-renders its reader every `intervalMs`, so "через 2 ч" and the
// field's too-soon check keep up with real time while a dialog stays open.
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => {
      setNow(Date.now());
    }, intervalMs);
    return () => {
      window.clearInterval(id);
    };
  }, [intervalMs]);
  return now;
}
