import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App } from './App';
import { ConfirmDialogProvider } from './state/ConfirmDialogContext';
import { ProfileProvider } from './state/ProfileContext';
import { RoomProvider } from './state/RoomContext';
import './styles/global.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <ProfileProvider>
        <ConfirmDialogProvider>
          <RoomProvider>
            <App />
          </RoomProvider>
        </ConfirmDialogProvider>
      </ProfileProvider>
    </BrowserRouter>
  </StrictMode>,
);
