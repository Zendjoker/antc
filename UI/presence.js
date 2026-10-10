// Presentation-only focus mode. Speech, permissions and requests remain in the app shell.
(() => {
  const root = document.documentElement;
  const core = document.querySelector('#jarvis-expand'), exit = document.querySelector('#jarvis-exit');
  function focusMode(enabled) {
    root.classList.toggle('jarvis-full', enabled);
    core.setAttribute('aria-pressed', String(enabled));
    core.setAttribute('aria-label', enabled ? 'Exit full ZEND mode' : 'Enter full ZEND mode');
    core.querySelector('span').textContent = enabled ? 'Exit full ZEND' : 'Enter full ZEND';
    exit.hidden = !enabled;
    window.JarvisHUD?.enable(enabled);
  }
  core.addEventListener('click', () => {
    if (root.classList.contains('jarvis-full') && window.JarvisHUD) window.JarvisHUD.menu();
    else focusMode(!root.classList.contains('jarvis-full'));
  });
  exit.addEventListener('click', () => { focusMode(false); core.focus(); });
  document.addEventListener('keydown', e => {
    if(e.key === 'Escape' && root.classList.contains('jarvis-full')) { focusMode(false); core.focus(); }
  });
  document.addEventListener('jarvis:leave', () => focusMode(false));
  // Explicit browser-independent entry link; ordinary home remains unchanged.
  window.addEventListener('jarvis:hud-ready', () => {
    if (new URLSearchParams(location.search).get('view') === 'jarvis') focusMode(true);
  });
})();
