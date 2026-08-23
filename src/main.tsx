import '@fontsource-variable/ibm-plex-sans';
import '@fontsource-variable/jetbrains-mono';

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './App';
import { LicensesPage } from './components/LicensesPage';
import './styles/globals.css';

const route = window.location.pathname.replace(/\/+$/u, '') || '/';

createRoot(document.getElementById('root')!).render(
  <StrictMode>{route === '/licenses' ? <LicensesPage /> : <App />}</StrictMode>,
);
