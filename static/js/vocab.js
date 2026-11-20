/* Lug'at kartochkalari: so'zni ko'rsatish, "bildim / bilmadim" natijasini serverga yuborish (Leitner takrorlash). */
(function () {
  'use strict';
  var root = document.getElementById('vocab');
  if (!root) { return; }
  var items = Array.prototype.slice.call(root.querySelectorAll('.vocab-item'));
  var progress = document.getElementById('vocab-progress');
  var finish = document.getElementById('vocab-finish');
  var meta = document.querySelector('meta[name="csrf-token"]');
  var token = meta ? meta.getAttribute('content') : '';
  var index = 0;

  function show(i) {
    items.forEach(function (el, n) { el.classList.toggle('hidden', n !== i); });
    if (i >= items.length) {
      progress.textContent = '';
      finish.textContent = root.dataset.tDone;
      finish.classList.remove('hidden');
      return;
    }
    progress.textContent = (i + 1) + ' / ' + items.length;
  }

  function send(wordId, known, done) {
    fetch(root.dataset.url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
      body: JSON.stringify({ word_id: wordId, known: known })
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (!data.ok) { throw new Error('fail'); }
      done();
    }).catch(function () { window.alert(root.dataset.tError); });
  }

  root.addEventListener('click', function (e) {
    var btn = e.target.closest('button[data-act]');
    if (!btn) { return; }
    var card = btn.closest('.vocab-item');
    if (btn.dataset.act === 'show') {
      card.querySelector('.vocab-back').classList.remove('hidden');
      btn.classList.add('hidden');
      card.querySelector('[data-act="known"]').classList.remove('hidden');
      card.querySelector('[data-act="unknown"]').classList.remove('hidden');
      return;
    }
    btn.disabled = true;
    send(parseInt(card.dataset.id, 10), btn.dataset.act === 'known', function () { index += 1; show(index); });
    window.setTimeout(function () { btn.disabled = false; }, 1500);
  });
  show(0);
})();
