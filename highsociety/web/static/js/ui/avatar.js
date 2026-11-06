// A player's square: their picture if they have one, otherwise the first letter
// of their name. CSS (.profile-chip-avatar img) crops any picture to the square,
// whatever its shape. A picture that fails to load falls back to the letter.
export function setAvatar(el, name, url) {
  // Already showing it (a cached paint followed by the fresh one): touching
  // anything would flash the letter.
  const current = el.querySelector('img');
  if (url && current && current.getAttribute('src') === url) return;
  const letter = (name || '?').charAt(0).toUpperCase();
  el.textContent = letter;
  if (!url) return;
  const img = document.createElement('img');
  img.alt = '';
  img.addEventListener('load', () => { el.textContent = ''; el.appendChild(img); });
  img.src = url;
}
