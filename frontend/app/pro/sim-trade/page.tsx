'use client';

import { useEffect, useState } from 'react';

export default function SimTradeDebug() {
  const [msg, setMsg] = useState('loading...');
  
  useEffect(() => {
    fetch('https://ai-buffett-backend.onrender.com/api/v1/sim/account')
      .then(r => r.json())
      .then(d => setMsg('OK: ' + JSON.stringify(d).slice(0, 100)))
      .catch(e => setMsg('Error: ' + e.message));
  }, []);

  return <div className="p-8 text-text"><pre>{msg}</pre></div>;
}
