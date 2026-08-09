# What's Left in the Backend

A survey of what's genuinely incomplete, stubbed, or unbuilt, based on reading the current code —
not a wishlist. Every item below was verified against the actual source (grepped for real usage,
not assumed). Written 2026-08-02, after the CLI fix pass, network protocol rewrite, seed/record/
replay system, and Transport/protocol modularity refactor.


# To be resolved now: 
- In all these, make sure to implement in appropriate color palette and theme. Don't deviate from the current theme.
- when a user resigns, instead of showing a popup, can you show like in screen dialog box like chess.com: Are you sure you want to resign: cancel, yes boxes.
- The same is applicable if the user presses the HighSociety button in middle of a game. Right now there is a popup. Instead of that, a box with these texts would be better UI.
- Player should be able to edit their username and display name by clicking: Playing as {player} box on top left corner also.
- If possible, just show Vignesh instead of {playing as Vignesh}. Cause that's little confusing. by default, keep it like, "guest" or something like that. So that user knows it's editable. also, it's kinda small. So, people won't know it's editable. Is there a way to make it look better. In chess.com, it's in bottom left. In social media, I think profile name stuff will be on right. Whatever you think is the right call and reasonable size, do it. But it should be understandable that's where all profile related stuff resides. 

- Currently when I save the link as a bookmark, there is no thumbnail. Would prefer a Sleek thumb nail. Potential Idea: the spade symbol you have kept in the game page. Another: stylized text "HS". implement this if possible.

- Very important, remember. Make sure to implement in appropriate color palette and theme. Don't deviate from the current theme.



# To be resolved later.
## Config values that are silently ignored

- **A recording doesn't pin the config it was made under.** `SessionRecorder` saves the seed, but
  not a snapshot/hash of `HSConfig.json` at record time. If painting values, disgrace card counts,
  etc. change before you replay, the replay could silently diverge from the original game (or hit
  a `ReplayMismatch`) with no clear "your config changed" error message.


## Bigger unbuilt capabilities

- **No mid-crash resume.** Record/replay reproduces a game from scratch, decision by decision — it
  doesn't snapshot in-progress state, so a server crash mid-game loses that game permanently (the
  recording file, if `--record` was on, only has *decisions made so far*, not a resumable state).

## Missing test coverage

- No tests for the connection-acceptance edge cases above (heartbeat timeout actually kicking a
  stale player, concurrent connection races).
- No load/stress testing (many concurrent spectators, rapid reconnect attempts).



Real-life game:
- if a player is passed, make a [pass] mark in opponents tile in the game play. 
- in the very first round, let the money be visible (but greyed) even if its' not the player's chance.
- in the very first bid, I can't see the player who joined in the opponents tab. But the moment the second person bids, I can see the person. I can see other bots who have joined tho. Fix that. 
- After the auction ends, Let's say A wins the auction, but B bidded till end but passed. When the second round starts, A starts the round. But B is unable to see her cash that should have refunded when it's greyed out. But the moment her turn comes, it's back to normal. So, the game implementation is correct, but UI is incorrect and makes her doubtful suddenly. 

- The review my friend said: At the end of each auction, the event stream should be a little more slow: like say who got which card should be visible for a bit longer. And then the new card should be announced. So that it's more understandable. Especially, when the auction ends and the 4th green card is revealed, it just happens too fast. she doesn't understand who took the previous card, spent how much and what was the 4th green card that got revealed. And suddenly the end page comes. I think after every auction ends, it should be clear who is getting the card for how much and then only the next card comes. By this way, this could be avoided. 

- Capitalize the first name of the bot's name in the config file. it should be "Marble" and not "marble"

- write the total calculation clearly. Like -5 happens before multiplying in how to play guide. 



