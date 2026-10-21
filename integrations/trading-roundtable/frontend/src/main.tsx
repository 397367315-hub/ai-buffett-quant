import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './v1.css';          // 由 styles.css 作用域化生成（.v1-shell）
import './legacy/legacy.css';   // 上一版五页的样式（.legacy-shell）
import './hybrid.css';       // 两套外壳共用的最外层规则

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
