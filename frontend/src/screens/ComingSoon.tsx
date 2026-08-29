// Placeholder for screens this phase's checkpoint doesn't cover yet
// (Leaderboard/Achievements/Account/My Games/Room all land in later
// phases per the migration plan) -- keeps every sidebar link and route
// real and navigable instead of 404ing mid-review.
export function ComingSoon({ title }: { title: string }) {
  return (
    <div className="card panel panel--centered">
      <h2>{title}</h2>
      <p className="muted">Coming in a later phase of the React migration.</p>
    </div>
  );
}
