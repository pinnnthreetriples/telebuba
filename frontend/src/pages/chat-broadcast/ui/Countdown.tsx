// Ticks down to the next message of a chat. Owns its one-second clock so the board
// around it does not re-render every second.
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

const TICK_MS = 1000;

function formatLeft(seconds: number): string {
  const whole = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

export function Countdown({ to }: { to: number }) {
  const { t } = useTranslation();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, TICK_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, []);
  const left = (to - now) / 1000;
  return (
    <span className="tabular-nums">
      {left > 0
        ? t('chatBroadcast.when.nextIn', { time: formatLeft(left) })
        : t('chatBroadcast.when.sending')}
    </span>
  );
}
