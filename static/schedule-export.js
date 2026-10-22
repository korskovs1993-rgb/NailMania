(() => {
  const source = document.querySelector('#schedule-data');
  if (!source) return;
  const data = JSON.parse(source.textContent);
  const status = document.querySelector('#export-status');
  const width = 1080, margin = 70;
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  const wrap = (text, max, font) => {
    ctx.font = font;
    const lines = []; let line = '';
    for (const word of String(text).split(' ')) {
      if (ctx.measureText(line + word).width > max && line) { lines.push(line.trim()); line = ''; }
      // Long URLs and single words also wrap to the canvas width.
      for (const char of word + ' ') {
        if (ctx.measureText(line + char).width > max && line) { lines.push(line.trim()); line = ''; }
        line += char;
      }
    }
    if (line.trim()) lines.push(line.trim());
    return lines;
  };
  const blocks = [
    {text: 'NEILMANIA', font: '600 26px sans-serif', gap: 28},
    {text: data.name + ' · свободные окошки', font: '600 52px sans-serif', gap: 24},
    {text: data.city + ' · UTC+10', font: '28px sans-serif', gap: 16},
    {text: data.service, font: '30px sans-serif', gap: 30},
    {text: data.start_times ? 'Время начала · шаг 15 минут' : 'Свободные интервалы · длительность уточни у мастера', font: '26px sans-serif', gap: 35},
  ];
  for (const day of data.days) {
    blocks.push({text: day.label, font: '600 32px sans-serif', gap: 8});
    blocks.push({text: day.windows.join('    ·    ') || (day.closed ? 'Выходной' : 'Нет свободных окошек'), font: '32px sans-serif', gap: 36});
  }
  blocks.push({text: data.url ? 'Запись: ' + data.url : 'Для записи напиши мастеру', font: '28px sans-serif', gap: 22});
  blocks.push({text: 'Актуально на ' + data.created + ' (UTC+10). Запись после подтверждения.', font: '24px sans-serif', gap: 0});
  let height = margin * 2;
  for (const block of blocks) {
    block.lines = wrap(block.text, width - margin * 2, block.font);
    block.lineHeight = Number(block.font.match(/(\d+)px/)[1]) * 1.4;
    height += block.lines.length * block.lineHeight + block.gap;
  }
  canvas.width = width; canvas.height = Math.ceil(height);
  const gradient = ctx.createLinearGradient(0, 0, width, height);
  gradient.addColorStop(0, '#f2ebff'); gradient.addColorStop(1, '#eef7f2');
  ctx.fillStyle = gradient; ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = 'rgba(255,255,255,.72)'; ctx.fillRect(30, 30, width - 60, height - 60);
  ctx.textBaseline = 'top'; ctx.fillStyle = '#302a40';
  let y = margin;
  for (const block of blocks) {
    ctx.font = block.font;
    for (const line of block.lines) { ctx.fillText(line, margin, y); y += block.lineHeight; }
    y += block.gap;
  }
  let file;
  canvas.toBlob(blob => { if (blob) file = new File([blob], 'neilmania-free-times.png', {type:'image/png'}); }, 'image/png');
  const download = () => {
    if (!file) { status.textContent = 'Картинка готовится. Нажми ещё раз через секунду.'; return; }
    const url = URL.createObjectURL(file), link = document.createElement('a');
    link.href = url; link.download = file.name; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    status.textContent = 'Картинка сохранена в загрузки.';
  };
  document.querySelector('#save-schedule').addEventListener('click', download);
  document.querySelector('#share-schedule').addEventListener('click', async () => {
    if (!file) { status.textContent = 'Картинка готовится. Попробуй ещё раз.'; return; }
    if (!navigator.canShare?.({files:[file]})) { download(); return; }
    try { await navigator.share({files:[file], title:'Свободные окошки NeilMania'}); }
    catch (error) { if (error.name !== 'AbortError') status.textContent = 'Не получилось поделиться. Сохрани картинку кнопкой рядом.'; }
  });
})();
