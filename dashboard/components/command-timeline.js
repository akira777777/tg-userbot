"use client";

import { useEffect, useState } from 'react';
import { apiRequest } from '@/lib/api';

export default function CommandTimeline({ initData }) {
  const [commands, setCommands] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!initData) return;
    setLoading(true);
    apiRequest('/api/control/commands', { initData })
      .then((data) => setCommands(data.commands || []))
      .catch((err) => console.error('загрузка команд:', err.message))
      .finally(() => setLoading(false));
  }, [initData]);

  const statusColor = (s) => {
    switch (s) {
      case 'SUCCEEDED': return '#6ee7a0';
      case 'FAILED': return '#ff8fa3';
      case 'RUNNING': return '#ffd28a';
      case 'CANCELLED': return '#8b98a9';
      default: return '#8b98a9';
    }
  };

  if (loading) {
    return <div style={{ padding: 20, color: '#8b98a9' }}>Загрузка команд…</div>;
  }

  return (
    <div style={{ padding: 12 }}>
      <h3 style={{ margin: '0 0 10px', fontSize: 16, color: '#7fd1ff' }}>История команд</h3>
      {commands.length === 0 ? (
        <div style={{ color: '#8b98a9', fontSize: 13 }}>Команд пока нет</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {commands.slice(0, 20).map((cmd) => (
            <div
              key={cmd.id}
              style={{
                background: '#121722',
                border: '1px solid #2a3548',
                borderRadius: 10,
                padding: 10,
                fontSize: 13,
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ fontWeight: 600, color: '#dbe2ea' }}>{cmd.kind}</span>
                <span
                  style={{
                    color: statusColor(cmd.status),
                    fontWeight: 600,
                    fontSize: 11,
                    textTransform: 'uppercase',
                  }}
                >
                  {cmd.status}
                </span>
              </div>
              <div style={{ color: '#8b98a9', fontSize: 12 }}>
                {cmd.account || 'main'} · {new Date(cmd.created_at).toLocaleString()}
              </div>
              {cmd.error_code && (
                <div style={{ color: '#ff8fa3', fontSize: 11, marginTop: 4 }}>{cmd.error_code}</div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
