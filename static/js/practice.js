/* Mashq rejimi: savollar serverdan birma-bir keladi; to'g'ri javob faqat javobdan keyin qaytadi. Reytingga ta'sir qilmaydi. */
(function () {
  'use strict';
  var root = document.getElementById('practice');
  if (!root) { return; }
  var T = JSON.parse(root.getAttribute('data-i18n') || '{}');
  var meta = document.querySelector('meta[name="csrf-token"]');
  var token = meta ? meta.getAttribute('content') : '';
  var el = function (id) { return document.getElementById(id); };
  var current = null;
  var locked = false;

  function show(node, on) { node.classList.toggle('hidden', !on); }

  function api(url, body) {
    var opts = { credentials: 'same-origin', headers: { 'Accept': 'application/json' } };
    if (body) {
      opts.method = 'POST';
      opts.headers['Content-Type'] = 'application/json';
      opts.headers['X-CSRF-Token'] = token;
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) { return r.json(); });
  }

  function fail() { el('p-feedback').textContent = T.network; el('p-feedback').className = 'flash flash-error'; locked = false; }

  function render(data) {
    show(el('p-feedback'), false); show(el('p-explain'), false); show(el('p-next'), false);
    if (!data.ok) { return fail(); }
    if (!data.question) { return finish(data.correct, data.total); }
    current = data;
    locked = false;
    el('p-progress').textContent = (data.index + 1) + ' / ' + data.total;
    var q = data.question;
    el('p-text').textContent = q.text;
    el('p-passage').textContent = q.passage || '';
    show(el('p-passage'), !!q.passage);
    var listen = el('p-listen');
    show(listen, !!q.tts);
    listen.onclick = function () {
      if (!('speechSynthesis' in window)) { window.alert(T.no_tts); return; }
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(new window.SpeechSynthesisUtterance(q.tts));
    };
    var box = el('p-options');
    box.textContent = '';
    q.options.forEach(function (text, i) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'btn btn-block option'; b.textContent = text; b.dataset.i = String(i);
      b.addEventListener('click', function () { choose(i); });
      box.appendChild(b);
    });
  }

  function choose(i) {
    if (locked || !current) { return; }
    locked = true;
    api(root.dataset.answerUrl, { index: current.index, choice: i }).then(function (res) {
      if (!res.ok) { return fail(); }
      Array.prototype.forEach.call(el('p-options').children, function (b, n) {
        b.disabled = true;
        if (n === res.correct_index) { b.classList.add('is-correct'); }
        else if (n === i) { b.classList.add('is-wrong'); }
      });
      var fb = el('p-feedback');
      fb.textContent = res.is_correct ? T.correct : T.wrong;
      fb.className = 'flash ' + (res.is_correct ? 'flash-success' : 'flash-error');
      if (res.explanation) { el('p-explain').textContent = res.explanation; show(el('p-explain'), true); }
      if (res.done) { current.last = res; }
      el('p-next').onclick = function () { res.done ? finish(res.correct, res.total) : load(); };
      show(el('p-next'), true);
    }).catch(fail);
  }

  function finish(correct, total) {
    ['p-options', 'p-text', 'p-passage', 'p-listen', 'p-next', 'p-feedback', 'p-explain', 'p-progress'].forEach(function (id) { show(el(id), false); });
    el('p-final-text').textContent = T.finished + ': ' + correct + ' / ' + total;
    show(el('p-final'), true);
  }

  function load() { api(root.dataset.stateUrl).then(render).catch(fail); }
  load();
}());
