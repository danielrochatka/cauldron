(function () {
  'use strict';
  var form = document.getElementById('ai-form');
  var responseArea = document.getElementById('ai-response-area');
  var responseEl = document.getElementById('ai-response');
  if (!form) { return; }
  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (responseArea) { responseArea.hidden = false; }
    if (responseEl) { responseEl.textContent = 'Working...'; }
    var textEl = document.getElementById('ai-request');
    var text = textEl ? textEl.value : '';
    var csrfEl = form.querySelector('input[name=csrfmiddlewaretoken]');
    var csrf = csrfEl ? csrfEl.value : '';
    fetch(window.location.pathname, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrf,
      },
      body: JSON.stringify({ request: text }),
      credentials: 'same-origin'
    }).then(function (r) {
      return r.json().then(function (body) {
        if (responseEl) { responseEl.textContent = JSON.stringify(body, null, 2); }
      });
    }).catch(function (err) {
      if (responseEl) { responseEl.textContent = 'Error: ' + err; }
    });
  });
}());
