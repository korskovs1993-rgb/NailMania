(() => {
  const dialog = document.querySelector('#catalog-editor');
  if (!dialog) return;
  const closeLink = dialog.querySelector('.editor-close');
  dialog.removeAttribute('open');
  dialog.showModal();
  document.body.classList.add('catalog-modal-open');
  dialog.addEventListener('cancel', event => { event.preventDefault(); location.assign(closeLink.href); });
  dialog.addEventListener('click', event => {
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) location.assign(closeLink.href);
  });
  dialog.querySelector('[aria-invalid="true"], input:not([type="hidden"]), select')?.focus({preventScroll:true});
})();
