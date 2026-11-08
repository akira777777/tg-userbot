"use client";

import { useState } from 'react';

export default function CommandForm({ action, onSubmit, initData }) {
  const [text, setText] = useState('');
  const [delayMin, setDelayMin] = useState('20');
  const [delayMax, setDelayMax] = useState('45');
  const [limit, setLimit] = useState('200');
  const [repeatOk, setRepeatOk] = useState(false);
  const [dryRun, setDryRun] = useState(true);
  [action, onSubmit, initData];

  const handleSubmit = (e) => {
    e.preventDefault();
    const command = {
      kind: action.kind === 'LIVE' ? 'BROADCAST' : 'READ',
      mode: action.kind,
      account: 'main',
      payload: {
        text,
        delay_min: parseFloat(delayMin) || 0,
        delay_max: parseFloat(delayMax) || 0,
        group_ids: [],
        limit: parseInt(limit, 10) || 200,
        repeat_ok: repeatOk,
        dry_run: dryRun,
      },
      idempotency_key: crypto.randomUUID(),
    };

    if (action.kind === 'LIVE') {
      command.mode = 'LIVE';
    }

    onSubmit(command);
  };

  return (
    <div style={{
      background: 'var(--tg-theme-secondary-bg-color, #121722)',
      border: '1px solid #2a3548',
      borderRadius: 12,
      padding: 16,
      margin: 12,
    }}>
      <h3 style={{ margin: '0 0 12px', fontSize: 16, color: '#7fd1ff' }}>Конфигурация рассылки</h3>

      <div style={{ marginBottom: 12 }}>
        <label style={{ display: 'block', fontSize: 12, color: '#8b98a9', marginBottom: 4 }}>Текст сообщения</label>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          style={{
            width: '100%',
            background: '#0b0e14',
            color: '#dbe2ea',
            border: '1px solid #2a3548',
            borderRadius: 8,
            padding: 8,
            fontFamily: 'inherit',
          }}
        />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 12, marginBottom: 12 }}>
        <div>
          <label style={{ display: 'block', fontSize: 12, color: '#8b98a9', marginBottom: 4 }}>Задержка мин (с)</label>
          <input
            type="number"
            value={delayMin}
            onChange={(e) => setDelayMin(e.target.value)}
            style={{
              width: '100%',
              background: '#0b0e14',
              color: '#dbe2ea',
              border: '1px solid #2a3548',
              borderRadius: 8,
              padding: 8,
            }}
          />
        </div>
        <div>
          <label style={{ display: 'block', fontSize: 12, color: '#8b98a9', marginBottom: 4 }}>Задержка макс (с)</label>
          <input
            type="number"
            value={delayMax}
            onChange={(e) => setDelayMax(e.target.value)}
            style={{
              width: '100%',
              background: '#0b0e14',
              color: '#dbe2ea',
              border: '1px solid #2a3548',
              borderRadius: 8,
              padding: 8,
            }}
          />
        </div>
        <div>
          <label style={{ display: 'block', fontSize: 12, color: '#8b98a9', marginBottom: 4 }}>Лимит</label>
          <input
            type="number"
            value={limit}
            onChange={(e) => setLimit(e.target.value)}
            style={{
              width: '100%',
              background: '#0b0e14',
              color: '#dbe2ea',
              border: '1px solid #2a3548',
              borderRadius: 8,
              padding: 8,
            }}
          />
        </div>
      </div>

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: '#dbe2ea', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={repeatOk}
            onChange={(e) => setRepeatOk(e.target.checked)}
            style={{ accentColor: '#7fd1ff' }}
          />
          Повторно тем же пользователям
        </label>
      </div>

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: '#dbe2ea', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)}
            style={{ accentColor: '#7fd1ff' }}
          />
          Режим предпросмотра (без отправки)
        </label>
      </div>

      <button
        onClick={handleSubmit}
        style={{
          width: '100%',
          padding: '12px',
          borderRadius: 10,
          border: '0',
          background: dryRun ? '#2f81f7' : '#123524',
          color: '#fff',
          fontWeight: 600,
          fontSize: 14,
          cursor: 'pointer',
        }}
      >
        {dryRun ? 'Предпросмотр (dry-run)' : 'Запустить рассылку'}
      </button>
    </div>
  );
}
