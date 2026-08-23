# CLAUDE.md

Project-specific instructions for Claude Code when working on this repo.

## Visual/CSS fixes: check the whole screen, not just the reported element

When asked to fix a visual issue (alignment, spacing, sizing) on one
specific element, treat that as a prompt to review the *whole screen or
panel* it lives on for the same class of problem — not just the one
element named. A narrow fix can leave sibling elements looking
inconsistent with it, and with the rest of the app's screens, even though
nobody has flagged them yet.

Concrete example from this project: asked to fix the matchmaking screen's
back-button alignment (it was centered instead of top-left like every
other screen's back link), the root cause was `#screen-matchmaking .panel`
setting `text-align: center` on the whole panel. Fixing only the back
button left the "Players" label pinned left by its own separate override
while its input stayed centered by a third rule — a mismatch nobody had
pointed at yet, visible in the very same screenshot taken to verify the
first fix, that got reported separately after the first "fix" shipped.

Before calling a visual fix complete:
1. Take a full screenshot of the affected screen (not just the element).
2. Actually look at it — don't just confirm the one reported symptom is
   gone.
3. Compare it against sibling screens/components using the same visual
   pattern (other back links, other form fields, other panels) for
   consistency, since a shared CSS rule affecting one often affects others
   the same way.
