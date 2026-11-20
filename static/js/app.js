/* Umumiy skript: mobil menyu, tasdiqlash oynalari, avtomatik yangilanish, til/rejim formalari. Inline skript yo'q (CSP). */
(function () {
  'use strict';
  document.addEventListener('click', function (e) {
    var toggle = e.target.closest('[data-toggle-nav]');
    if (toggle) { document.body.classList.toggle('nav-open'); return; }
    if (e.target.classList.contains('scrim')) { document.body.classList.remove('nav-open'); }
  });
  // <form data-confirm="Matn"> yoki <button data-confirm="Matn">
  document.addEventListener('submit', function (e) {
    var node = e.submitter && e.submitter.dataset.confirm ? e.submitter : e.target;
    var message = node.dataset && node.dataset.confirm;
    if (message && !window.confirm(message)) { e.preventDefault(); }
  });
  // <select data-autosubmit> o'zgarganda formani yuboradi
  document.addEventListener('change', function (e) {
    if (e.target.matches('[data-autosubmit]') && e.target.form) { e.target.form.submit(); }
  });
  // Sahifani vaqti-vaqti bilan yangilash: <body data-refresh="15">
  var refresh = parseInt(document.body.dataset.refresh || '0', 10);
  if (refresh > 0) {
    setTimeout(function () {
      if (!document.hidden && !document.querySelector('input:focus, textarea:focus')) { window.location.reload(); }
      else { setTimeout(function () { window.location.reload(); }, refresh * 1000); }
    }, refresh * 1000);
  }
  // Tezkor Battle takliflari: o'quvchi sahifada turganida "Sizga mos raqib topildi" xabari
  var box = document.getElementById('quick-invite');
  if (box && box.dataset.url) {
    var seen = {};
    var poll = function () {
      fetch(box.dataset.url, { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (data) {
        (data.items || []).forEach(function (it) {
          if (seen[it.id]) { return; }
          seen[it.id] = true;
          var a = document.createElement('a');
          a.className = 'flash flash-info link-block';
          a.href = box.dataset.base + it.id;
          a.textContent = box.dataset.text.replace('{name}', it.from);
          box.appendChild(a);
        });
      }).catch(function () { /* tarmoq xatosi: keyingi urinishda */ });
    };
    poll();
    setInterval(poll, 8000);
  }
})();
