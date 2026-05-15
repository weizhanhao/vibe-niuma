import React from 'react';
import { createRoot } from 'react-dom/client';
import { applyTheme, readThemeSync } from '../lib/theme';
import { App } from './App';

// 在 React 挂载前同步应用主题，避免浅 → 深的「白闪」
applyTheme(readThemeSync());

const el = document.getElementById('root');
if (el) createRoot(el).render(<React.StrictMode><App /></React.StrictMode>);
