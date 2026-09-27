import { useEffect, useState } from 'react';

const fmt = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit', second: '2-digit',
});

interface Props {
  source: 'demo' | 'api';
  error: string | null;
}

export function Header({ source, error }: Props) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <header className="header">
      <div className="brand">
        <div className="brand__mark" aria-hidden="true">
          <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
            <rect x="3" y="3" width="16" height="12" rx="3" stroke="currentColor" strokeWidth="1.8" />
            <path d="M3 10h16" stroke="currentColor" strokeWidth="1.8" />
            <circle cx="7" cy="18" r="1.6" fill="currentColor" />
            <circle cx="15" cy="18" r="1.6" fill="currentColor" />
          </svg>
        </div>
        <div className="brand__text">
          <span>Предиктор</span>
          <span>графика движения</span>
        </div>
      </div>

      <h1 className="header__title">Пульт диспетчера</h1>

      <div className="header__right">
        <label className="city">
          <span className="sr-only">Регион</span>
          <select id="city" defaultValue="msk">
            <option value="msk">Москва</option>
          </select>
        </label>
        <span className="clock">{fmt.format(now)} МСК</span>
        {error ? (
          <span className="badge badge--err" title={error}>Нет связи</span>
        ) : (
          <span className="badge badge--live">
            <i aria-hidden="true" />
            {source === 'api' ? 'Live' : 'Демо'}
          </span>
        )}
      </div>
    </header>
  );
}
