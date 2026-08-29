// True once Google Identity Services' external script has actually
// loaded -- window.google.accounts doesn't exist until then. Covers both
// orderings: the script finishing before this hook ever runs (checked
// once at mount) and finishing after (its own onload in index.html calls
// window.__hsGoogleReady).
import { useEffect, useState } from 'react';

export function useGoogleReady(): boolean {
  const [ready, setReady] = useState(() => !!window.google?.accounts);
  useEffect(() => {
    if (ready) return;
    window.__hsGoogleReady = () => setReady(true);
    return () => { delete window.__hsGoogleReady; };
  }, [ready]);
  return ready;
}
