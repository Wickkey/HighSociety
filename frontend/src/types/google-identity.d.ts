// Minimal ambient types for the bits of Google Identity Services (the
// external gsi/client script loaded in index.html) this app actually
// calls -- not the full GIS surface, just initialize/renderButton and the
// credential-response shape handed to our callback.
export {};

declare global {
  interface Window {
    // Called from index.html's GSI <script> tag's own onload attribute --
    // the script is a plain classic <script>, not an ES module, so it has
    // no other way to tell the React app it's actually ready. See
    // hooks/useGoogleReady.ts.
    __hsGoogleReady?: () => void;
    google?: {
      accounts: {
        id: {
          initialize(config: { client_id: string; callback: (response: { credential: string }) => void }): void;
          renderButton(parent: HTMLElement, options: { theme: string; size: string; width: number; text: string }): void;
        };
      };
    };
  }
}
