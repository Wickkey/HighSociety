// Google's own "Continue with Google" button, rendered into every element
// marked data-google-button: the login screen's, and the "Connect Google"
// spots a guest sees on Account and Achievements. Every one shares the
// single callback login.js's initGoogleSignIn registered -- that callback
// decides sign-in vs connect by whether a guest is already signed in here.
// A no-op until Google's script has loaded (initGoogleSignIn calls this
// again once it has), and everywhere GOOGLE_CLIENT_ID isn't configured.
const BUTTON_OPTIONS = { theme: 'filled_black', size: 'large', width: 280, text: 'continue_with' };

// Whether this deployment offers Google sign-in at all -- set inline by
// index.html, so it's known before Google's own script arrives. Decides
// whether a "Connect Google" block shows; its button slot keeps its height
// until renderGoogleButtons fills it (see .connect-google-button).
export function googleSignInConfigured() {
  return !!window.GOOGLE_CLIENT_ID;
}

export function renderGoogleButtons() {
  if (!googleSignInConfigured() || !window.google || !window.google.accounts) return;
  document.querySelectorAll('[data-google-button]').forEach((el) => {
    google.accounts.id.renderButton(el, BUTTON_OPTIONS);
  });
}
