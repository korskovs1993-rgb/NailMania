(() => {
  const root = document.documentElement;
  const systemDark = matchMedia('(prefers-color-scheme: dark)');
  const resolveColor = () => { root.dataset.color = root.dataset.colorMode === 'system' ? (systemDark.matches ? 'dark' : 'light') : (root.dataset.colorMode || 'palette'); };
  resolveColor(); systemDark.addEventListener('change', resolveColor);
  const select = document.querySelector('#theme-select');
  const themes = ['rose', 'sage', 'lavender', 'sand', 'ocean', 'night'];
  let saved = document.documentElement.dataset.theme || 'lavender';
  if (document.documentElement.dataset.account !== '1') {
    try { saved = localStorage.getItem('nailbook-theme') || saved; } catch (_) {}
  }
  const applyTheme = value => {
    document.documentElement.dataset.theme = themes.includes(value) ? value : 'rose';
    if (select) select.value = document.documentElement.dataset.theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', getComputedStyle(document.documentElement).getPropertyValue('--accent').trim());
  };
  applyTheme(saved);
  select?.addEventListener('change', () => {
    applyTheme(select.value);
    try { localStorage.setItem('nailbook-theme', select.value); } catch (_) {}
  });
  document.querySelectorAll('[data-confirm]').forEach(form => form.addEventListener('submit', event => {
    if (!confirm(form.dataset.confirm)) event.preventDefault();
  }));
  const form = document.querySelector('form#booking-form, form#move-form');
  if (form) {
    const loginButton = form.querySelector('[name="intent"][value="login"]');
    if (loginButton) document.querySelectorAll('a[href^="/account/login/"]').forEach(link => link.addEventListener('click', event => {
      event.preventDefault(); form.requestSubmit(loginButton);
    }));
    const date = form.querySelector('#id_date');
    const time = form.querySelector('#id_time');
    const slots = form.querySelector('#slots');
    let controller;
    let sequence = 0;
    const load = async () => {
      const current = ++sequence;
      controller?.abort(); controller = new AbortController();
      const params = new URLSearchParams({ date: date.value });
      form.querySelectorAll('[name="services"]:checked').forEach(input => params.append('services', input.value));
      if (form.dataset.token) params.set('token', form.dataset.token);
      slots.replaceChildren();
      if (!date.value || (!form.dataset.token && !params.has('services'))) {
        slots.textContent = form.dataset.token ? 'Выбери новую дату — покажем свободное время.' : 'Выбери услуги и дату — покажем свободное время.'; return;
      }
      slots.textContent = 'Проверяем свободное время…';
      try {
        const response = await fetch('/slots/?' + params, { cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw new Error('load');
        const data = await response.json();
        if (current !== sequence) return;
        slots.replaceChildren();
        const duration = document.querySelector('#duration');
        if (duration) duration.textContent = 'Длительность визита: ' + data.minutes + ' мин';
        if (!data.slots.length) slots.textContent = 'На эту дату свободного времени нет. Попробуй другой день.';
        for (const value of data.slots) {
          const button = document.createElement('button');
          button.type = 'button'; button.className = 'slot'; button.textContent = value;
          button.setAttribute('aria-pressed', String(time.value === value));
          if (time.value === value) button.classList.add('active');
          button.addEventListener('click', () => {
            time.value = value;
            slots.querySelectorAll('button').forEach(b => { b.classList.remove('active'); b.setAttribute('aria-pressed', 'false'); });
            button.classList.add('active'); button.setAttribute('aria-pressed', 'true');
          });
          slots.append(button);
        }
      } catch (error) {
        if (error.name !== 'AbortError') slots.textContent = 'Не удалось проверить время. Проверь интернет и выбери дату ещё раз.';
      }
    };
    date.addEventListener('change', () => { time.value = ''; load(); });
    form.querySelectorAll('[name="services"]').forEach(input => input.addEventListener('change', () => { time.value = ''; load(); }));
    load();
  }
  document.querySelector('#copy-link')?.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(location.href); document.querySelector('#copy-result').textContent = 'Ссылка скопирована.'; }
    catch (_) { document.querySelector('#copy-result').textContent = 'Скопируй адрес из адресной строки браузера.'; }
  });
  if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(() => {});
  if (location.pathname === '/studio/' || location.pathname === '/studio/inbox/' || location.pathname.startsWith('/s/')) {
    setInterval(() => {
      if (document.visibilityState === 'visible' && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName) && !document.querySelector('#move-form input[name="date"]')?.value) location.reload();
    }, 60000);
  }
})();

(() => {
const client = document.querySelector('#id_client');
const toggleClient = () => { const fields = document.querySelector('#new-client-fields'); if (!fields) return; fields.hidden = !!client.value; fields.querySelectorAll('input').forEach(input => {input.disabled = !!client.value;}); };
if (client) {client.addEventListener('change', toggleClient); toggleClient();}
const template = document.querySelector('#personal-template');
if (template) {
 const startDate = document.querySelector('#id_start_0'), startTime = document.querySelector('#id_start_1');
 const fillEnd = () => {const selected = template.selectedOptions[0]; if (!selected?.dataset.minutes || !startDate.value || !startTime.value) return;
 const end = new Date(startDate.value + 'T' + startTime.value + ':00Z'); end.setUTCMinutes(end.getUTCMinutes() + Number(selected.dataset.minutes));
 document.querySelector('#id_end_0').value = end.toISOString().slice(0,10); document.querySelector('#id_end_1').value = end.toISOString().slice(11,16);};
 template.addEventListener('change', () => {if (template.value) document.querySelector('#id_title').value = template.selectedOptions[0].dataset.title; fillEnd();});
 startDate.addEventListener('change', fillEnd); startTime.addEventListener('change', fillEnd);
}
})();

document.querySelectorAll('[data-public-day]').forEach(day => day.addEventListener('click', event => {const date = document.querySelector('#id_date'); if (!date) return; event.preventDefault(); date.value = day.dataset.publicDay; date.dispatchEvent(new Event('change', {bubbles:true})); document.querySelectorAll('[data-public-day]').forEach(cell => cell.classList.toggle('is-selected', cell === day)); document.querySelector('#booking-form').scrollIntoView({behavior:'smooth', block:'start'});}));
