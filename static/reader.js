/* Czytelnia: iframe z plikiem ksiazki + zapis postepu (PDF.js w iframe nie raportuje strony) */
(function () {
  const el = document.getElementById('reader');
  if (!el) return;
  const bookId = el.dataset.book;
  const type = el.dataset.type; // pdf | epub
  const frame = document.createElement('iframe');
  frame.src = '/books/' + encodeURIComponent(window.__BOOK_FILENAME__ || bookId);
  frame.style.cssText = 'width:100%;height:82vh;border:0;border-radius:12px';
  el.appendChild(frame);

  let page = parseInt(el.dataset.page || '0', 10);
  function save(useBeacon) {
    const payload = JSON.stringify({page: page, done: false});
    if (useBeacon) navigator.sendBeacon('/read/' + bookId + '/progress', new Blob([payload], {type: 'application/json'}));
    else fetch('/read/' + bookId + '/progress', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: payload});
  }
  const iv = setInterval(() => save(false), 15000);
  window.addEventListener('beforeunload', () => { clearInterval(iv); save(true); });
})();