/* Battle xonasi mijozi. Barcha hisob-kitob serverda; brauzer faqat tanlovni yuboradi va holatni ko'rsatadi.
   Aloqa uzilsa ham o'yin holati serverdan tiklanadi (har so'rovda to'liq holat keladi). */
(function () {
  'use strict';
  var root = document.getElementById('arena');
  if (!root) { return; }
  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  var T = {};
  try { T = JSON.parse(root.dataset.i18n || '{}'); } catch (err) { T = {}; }
  var initialStatus = root.dataset.status;
  var els = {
    countdown: document.getElementById('b-countdown'), play: document.getElementById('b-play'),
    qtext: document.getElementById('b-qtext'), passage: document.getElementById('b-passage'),
    options: document.getElementById('b-options'), timer: document.getElementById('b-timer'),
    info: document.getElementById('b-info'), explain: document.getElementById('b-explain'),
    me: document.getElementById('b-me'), opp: document.getElementById('b-opp'), listen: document.getElementById('b-listen'),
    qnum: document.getElementById('b-qnum')
  };
  var current = { index: -1, answered: false, deadline: 0, sent: false };
  var timerHandle = null;
  var busy = false;
  var terminal = { finished: 1, void: 1, no_show: 1, expired: 1, declined: 1, cancelled: 1 };

  function show(el, on) { if (el) { el.classList.toggle('hidden', !on); } }

  function api(url, body) {
    var opts = { credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf } };
    if (body) {
      opts.method = 'POST';
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) { return r.json(); });
  }

  function buildQuestion(state) {
    var q = state.question;
    current = { index: state.index, answered: !!state.answered, deadline: 0, sent: !!state.answered };
    if (els.qnum) { els.qnum.textContent = (state.index + 1) + ' / ' + state.total; }
    els.qtext.textContent = q.text;
    show(els.passage, !!q.passage);
    els.passage.textContent = q.passage || '';
    var ttsText = q.tts;
    show(els.listen, !!(ttsText || q.audio_url));
    els.listen.onclick = function () { speak(ttsText, q.audio_url); };
    els.options.textContent = '';
    q.options.forEach(function (text, i) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'opt';
      b.dataset.i = String(i);
      b.textContent = text;
      b.addEventListener('click', function () { choose(i); });
      els.options.appendChild(b);
    });
    show(els.explain, false);
    els.explain.textContent = '';
  }

  function speak(text, audioUrl) {
    if (audioUrl) { try { new Audio(audioUrl).play(); } catch (err) { /* ovoz yo'q */ } return; }
    if (!text || !('speechSynthesis' in window)) { els.info.textContent = T.no_tts || ''; return; }
    window.speechSynthesis.cancel();
    var u = new SpeechSynthesisUtterance(text);
    if (root.dataset.ttsLang) { u.lang = root.dataset.ttsLang; }
    u.rate = 0.9;
    window.speechSynthesis.speak(u);
  }

  function markChosen(i) {
    Array.prototype.forEach.call(els.options.children, function (b) {
      b.disabled = true;
      b.classList.toggle('chosen', i !== null && b.dataset.i === String(i));
    });
  }

  function choose(i) {
    if (current.answered || current.sent) { return; }
    current.answered = true;
    current.sent = true;
    markChosen(i);
    els.info.textContent = T.waiting || '';
    api(root.dataset.answer, { q_index: current.index, choice: i }).then(function (r) {
      if (!r.ok) { els.info.textContent = r.message || ''; }
    }).catch(function () { els.info.textContent = T.network || ''; current.sent = false; });
  }

  function startTimer(secondsLeft, total) {
    current.deadline = performance.now() + secondsLeft * 1000;
    els.timer.max = total * 1000;
    clearInterval(timerHandle);
    timerHandle = setInterval(function () {
      var left = Math.max(0, current.deadline - performance.now());
      els.timer.value = left;
      if (left <= 0) {
        clearInterval(timerHandle);
        if (!current.sent) {
          current.sent = true;
          current.answered = true;
          markChosen(null);
          api(root.dataset.answer, { q_index: current.index, choice: null }).catch(function () { /* server vaqtni o'zi hisoblaydi */ });
        }
      }
    }, 100);
  }

  function render(state) {
    if (!state || !state.ok) { return; }
    if (terminal[state.status]) { window.location.href = root.dataset.result; return; }
    if (state.status !== initialStatus) { window.location.reload(); return; }
    if (state.status !== 'active') { return; }
    if (els.me) { els.me.textContent = state.me_answered + ' / ' + state.total; }
    if (els.opp) { els.opp.textContent = state.opp_answered + ' / ' + state.total; }
    if (state.phase === 'countdown') {
      show(els.countdown, true); show(els.options, false); show(els.qtext, false); show(els.timer, false);
      els.countdown.textContent = Math.ceil(state.seconds) > 0 ? String(Math.ceil(state.seconds)) : (T.start || 'START');
      return;
    }
    show(els.countdown, false); show(els.options, true); show(els.qtext, true); show(els.timer, true);
    if (state.phase === 'finishing') { els.info.textContent = T.finishing || ''; return; }
    if (state.index !== current.index) { buildQuestion(state); }
    if (state.phase === 'question') {
      if (state.answered) { markChosen(state.my_choice); current.answered = true; current.sent = true; }
      startTimer(state.seconds_left, state.seconds_total);
      if (!state.answered) { els.info.textContent = ''; }
    } else if (state.phase === 'gap') {
      clearInterval(timerHandle);
      els.timer.value = 0;
      Array.prototype.forEach.call(els.options.children, function (b) {
        b.disabled = true;
        var i = parseInt(b.dataset.i, 10);
        b.classList.remove('chosen');
        if (i === state.correct_index) { b.classList.add('correct'); }
        else if (i === state.my_choice) { b.classList.add('wrong'); }
      });
      els.info.textContent = state.my_choice === null || state.my_choice === undefined ? (T.no_answer || '') :
        (state.was_correct ? (T.correct || '') : (T.wrong || ''));
      if (state.explanation) { els.explain.textContent = state.explanation; show(els.explain, true); }
    }
  }

  function poll() {
    if (busy || document.hidden) { return; }
    busy = true;
    api(root.dataset.state).then(render).catch(function () { /* tarmoq: keyingi urinish */ }).then(function () { busy = false; });
  }
  poll();
  setInterval(poll, initialStatus === 'active' ? 700 : 2500);
  document.addEventListener('visibilitychange', poll);
})();
