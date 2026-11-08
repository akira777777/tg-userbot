"use client";

import { useState } from 'react';
import { hapticImpact } from '@/lib/telegram';

const BUTTONS = [
  { id: 'scrape', label: 'Сбор информации', kind: 'READ' },
  { id: 'collect_users', label: 'Сбор юзеров', kind: 'READ' },
  { id: 'broadcast_chat', label: 'Рассылка по чатам', kind: 'LIVE' },
  { id: 'broadcast_channel', label: 'Рассылка по каналам', kind: 'LIVE' },
  { id: 'dm_users', label: 'Рассылка по юзерам', kind: 'LIVE' },
  { id: 'media_add', label: 'Добавить медиа', kind: 'READ' },
  { id: 'set_interval', label: 'Интервал', kind: 'READ' },
];

export default function ButtonBar({ onAction }) {
  const [active, setActive] = useState(null);

  return (
    <div style={{
      display: 'flex',
      flexWrap: 'wrap',
      gap: 8,
      padding: 12,
      background: 'var(--tg-theme-secondary-bg-color, #121722)',
    }}>
      {BUTTONS.map((btn) => (
        <button
          key={btn.id}
          onClick={() => {
            hapticImpact('light');
            setActive(btn.id);
            onAction(btn);
          }}
          style={{
            flex: 1,
            minWidth: '140px',
            padding: '10px 14px',
            borderRadius: 10,
            border: active === btn.id ? '2px solid #7fd1ff' : '1px solid #2a3548',
            background: active === btn.id ? '#1a2536' : '#121722',
            color: '#dbe2ea',
            fontSize: 13,
            cursor: 'pointer',
          }}
        >
          {btn.label}
          {btn.kind === 'LIVE' && <span style={{ fontSize: '10px', opacity: 0.6, marginLeft: 6 }}>⚠ подтв.</span>}
        </button>
      ))}
    </div>
  );
}
