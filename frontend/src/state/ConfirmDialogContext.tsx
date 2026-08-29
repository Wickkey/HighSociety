// App-wide promise-based confirm dialog -- ported from the old frontend's
// ui/modals.js confirmDialog/resolveConfirmDialog. Same shape as the
// native window.confirm() it replaces (`if (!(await confirm(...))) return`)
// but styled like the rest of the app, and only one can be open at a time
// (a second call while one is pending would just replace it -- no call site
// in this app opens two at once, same assumption the old singleton made).
import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';

interface PendingConfirm {
  message: string;
  confirmLabel: string;
  resolve: (ok: boolean) => void;
}

type ConfirmFn = (message: string, confirmLabel: string) => Promise<boolean>;

const ConfirmDialogContext = createContext<ConfirmFn | null>(null);

export function ConfirmDialogProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingConfirm | null>(null);

  const confirm = useCallback<ConfirmFn>((message, confirmLabel) => (
    new Promise<boolean>((resolve) => setPending({ message, confirmLabel, resolve }))
  ), []);

  const settle = (ok: boolean) => {
    pending?.resolve(ok);
    setPending(null);
  };

  return (
    <ConfirmDialogContext.Provider value={confirm}>
      {children}
      {pending && (
        <div className="modal-overlay">
          <div className="card modal">
            <p>{pending.message}</p>
            <div className="modal-actions">
              <button type="button" className="secondary" onClick={() => settle(false)}>Cancel</button>
              <button type="button" className="primary" onClick={() => settle(true)}>{pending.confirmLabel}</button>
            </div>
          </div>
        </div>
      )}
    </ConfirmDialogContext.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmDialogContext);
  if (!ctx) throw new Error('useConfirm must be used within a ConfirmDialogProvider');
  return ctx;
}
