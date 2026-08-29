// Mirrors the old frontend's auth/profile.js: a saved device identity is
// either a guest (googleId null) or a Google-linked account. `username` is
// the stable identifier used everywhere in the API/WS protocol; `name` is
// the (today always equal to username) display label -- kept as a separate
// field because the backend's own player rows distinguish username from
// display_name, even though nothing in this app currently lets them diverge.
export interface Profile {
  username: string;
  name: string;
  googleId: string | null;
}
