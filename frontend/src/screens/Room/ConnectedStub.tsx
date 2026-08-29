// Shared by PlayerPanel and LiveGamePlaceholder for any state past a
// successful identify (fresh 'waiting' once the table's full, 'reconnected',
// or 'game' -- real in-game messages already flowing) where the connection
// itself is healthy and it's genuinely Phase 3's job to render something.
// Splitting this out (rather than duplicating the same three-way phase
// check in both callers) keeps "what counts as connected" defined in one
// place.
export function ConnectedStub() {
  return (
    <div className="card panel">
      <h2>You&apos;re at the table</h2>
      <p className="muted">Live gameplay lands in Phase 3 -- for now, this just confirms the connection is live and receiving real game messages.</p>
    </div>
  );
}
