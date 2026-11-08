"use client";

import { useState } from 'react';

export default function OperationsDashboard() {
  const [activeTab, setActiveTab] = useState('overview');

  const tabs = [
    { id: 'overview', label: 'Обзор' },
    { id: 'accounts', label: 'Аккаунты' },
    { id: 'groups', label: 'Группы' },
  ];

  return (
    <div style={{ padding: 12 }}>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              padding: '6px 12px',
              borderRadius: 8,
              border: '1px solid #2a3548',
              background: activeTab === tab.id ? '#1a2536' : 'transparent',
              color: activeTab === tab.id ? '#7fd1ff' : '#8b98a9',
              fontSize: 13,
              cursor: 'pointer',
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'overview' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
          <div style={{ background: '#121722', border: '1px solid #2a3548', borderRadius: 12, padding: 14 }}>
            <div style={{ fontSize: 12, color: '#8b98a9' }}>Аккаунты</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: '#7fd1ff', marginTop: 6 }}>—</div>
          </div>
          <div style={{ background: '#121722', border: '1px solid #2a3548', borderRadius: 12, padding: 14 }}>
            <div style={{ fontSize: 12, color: '#8b98a9' }}>Группы в рассылке</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: '#7fd1ff', marginTop: 6 }}>—</div>
          </div>
          <div style={{ background: '#121722', border: '1px solid #2a3548', borderRadius: 12, padding: 14 }}>
            <div style={{ fontSize: 12, color: '#8b98a9' }}>Активные кампании</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: '#7fd1ff', marginTop: 6 }}>—</div>
          </div>
        </div>
      )}

      {activeTab === 'accounts' && (
        <div style={{ background: '#121722', border: '1px solid #2a3548', borderRadius: 12, padding: 14 }}>
          <div style={{ fontSize: 12, color: '#8b98a9', marginBottom: 10 }}>(данные загружаются из Supabase через bootstrap API)</div>
          <table style={{ width: '100%', fontSize: 13 }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left', color: '#8b98a9' }}>Аккаунт</div>
                <th style={{ textAlign: 'left', color: '#8b98a9' }}>Статус</th>
                <th style={{ textAlign: 'left', color: '#8b98a9' }}>DM сегодня</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td style={{ color: '#dbe2ea', fontFamily: 'monospace' }}>Аккаунт 1</td>
                <td style={{ color: '#6ee7a0' }}>активен</td>
                <td>0 / —</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {activeTab === 'groups' && (
        <div style={{ background: '#121722', border: '1px solid #2a3548', borderRadius: 12, padding: 14 }}>
          <div style={{ fontSize: 12, color: '#8b98a9', marginBottom: 10 }}>(группы загружаются из userbot.db через воркер)</div>
        </div>
      )}
    </div>
  );
}
