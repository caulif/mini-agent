(() => {
  const offset = () => document.documentElement.style.setProperty('--nav-offset', Math.ceil(document.querySelector('.top').getBoundingClientRect().height) + 24 + 'px');
  offset(); addEventListener('resize', offset);
  const languageSwitch = document.querySelector('.language-switch');
  const switchTarget = languageSwitch.dataset.target;
  const updateSwitch = () => {
    const target = new URL(switchTarget, location.href);
    target.hash = location.hash;
    languageSwitch.href = target.href;
  };
  updateSwitch(); addEventListener('hashchange', updateSwitch);
  const english = document.documentElement.lang === 'en';
  document.querySelectorAll('.copybtn').forEach(button => button.addEventListener('click', async event => {
    event.preventDefault(); event.stopPropagation();
    const text = button.closest('details').querySelector('td.code pre').textContent.replace(/\s+$/, '') + '\n';
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch {
      const input = document.createElement('textarea'); input.value = text;
      input.style.position = 'fixed'; input.style.top = '-9999px'; document.body.append(input);
      input.select(); ok = document.execCommand('copy'); input.remove();
    }
    button.textContent = ok ? (english ? 'Copied ✓' : '已复制 ✓') : (english ? 'Copy failed' : '复制失败');
    button.classList.toggle('done', ok); button.classList.toggle('bad', !ok);
    setTimeout(() => { button.textContent = english ? 'Copy' : '复制'; button.classList.remove('done', 'bad'); }, 1500);
  }));
})();
