(function () {
  'use strict';
  var form = document.getElementById('ai-form');
  var responseArea = document.getElementById('ai-response-area');
  var responseEl = document.getElementById('ai-response');
  if (!form) { return; }

  // Attachment upload state.
  var _uploadUrl = form.getAttribute('data-attachment-upload-url') || '';
  var _attachmentIds = [];  // successfully uploaded attachment UUIDs
  var _pendingUploads = 0;  // count of in-flight uploads

  var fileInput = document.getElementById('ai-attachments');
  var attachmentList = document.getElementById('ai-attachment-list');

  function _addAttachmentItem(file) {
    if (!attachmentList) { return null; }
    var li = document.createElement('li');
    li.className = 'cui-attachment-list__item';
    li.textContent = file.name + ' — uploading…';
    attachmentList.appendChild(li);
    return li;
  }

  function _uploadFile(file) {
    if (!_uploadUrl) { return; }
    _pendingUploads += 1;
    var li = _addAttachmentItem(file);
    var csrfEl = form.querySelector('input[name=csrfmiddlewaretoken]');
    var csrf = csrfEl ? csrfEl.value : '';
    var formData = new FormData();
    formData.append('file', file);
    fetch(_uploadUrl, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrf },
      body: formData,
      credentials: 'same-origin',
    }).then(function (r) {
      if (!r.ok) {
        return r.json().then(function (body) {
          var msg = (body && body.error) ? body.error : ('HTTP ' + r.status);
          if (li) { li.textContent = file.name + ' — upload failed: ' + msg; }
          _pendingUploads -= 1;
        }).catch(function () {
          if (li) { li.textContent = file.name + ' — upload failed (HTTP ' + r.status + ')'; }
          _pendingUploads -= 1;
        });
      }
      return r.json().then(function (body) {
        if (body && body.attachment_id) {
          _attachmentIds.push(body.attachment_id);
          if (li) { li.textContent = file.name + ' — ready'; }
        } else {
          if (li) { li.textContent = file.name + ' — unexpected server response'; }
        }
        _pendingUploads -= 1;
      });
    }).catch(function (err) {
      if (li) { li.textContent = file.name + ' — upload failed: ' + (err && err.message ? err.message : 'network error'); }
      _pendingUploads -= 1;
    });
  }

  if (fileInput) {
    fileInput.addEventListener('change', function () {
      var files = fileInput.files;
      for (var i = 0; i < files.length; i++) {
        _uploadFile(files[i]);
      }
      fileInput.value = '';  // allow re-selecting the same file
    });
  }

  // Maximum excerpt length to show for unexpected non-JSON bodies.
  // Never render full HTML — bound it to prevent leaking stack traces or secrets.
  var MAX_EXCERPT = 120;

  function _show(msg) {
    if (responseEl) { responseEl.textContent = msg; }
  }

  function _sanitize(text) {
    // Strip HTML tags and truncate — never render raw markup in the UI.
    return (text || '').replace(/<[^>]*>/g, '').trim().slice(0, MAX_EXCERPT);
  }

  function _handleResponse(r) {
    var status = r.status;
    var ct = (r.headers.get('content-type') || '').toLowerCase().split(';')[0].trim();
    var isHtml = ct === 'text/html' || ct === 'application/xhtml+xml';
    var isJson = ct === 'application/json';

    // --- Auth redirect: session expired or CSRF failure followed by redirect ---
    // fetch(credentials:'same-origin') follows redirects automatically.
    // response.redirected is true when the final URL differs from the request URL.
    if (r.redirected) {
      _show('Your session has expired or you have been redirected to the login page. ' +
            'Please refresh the page and log in again.');
      return;
    }

    // --- HTML response without redirect (CSRF 403, Django error page, proxy error) ---
    if (isHtml) {
      if (status === 403) {
        _show('Access denied (403). You do not have permission to use Admin AI, ' +
              'or your CSRF token is no longer valid. Please refresh the page.');
        return;
      }
      if (status === 404) {
        _show('Endpoint not found (404). Contact your administrator.');
        return;
      }
      if (status >= 500) {
        _show('Server error (HTTP ' + status + '). Contact your administrator.');
        return;
      }
      // Other HTML (e.g. proxy interception, maintenance page)
      return r.text().then(function (body) {
        var excerpt = _sanitize(body);
        _show('Unexpected response (HTTP ' + status + '). ' +
              (excerpt ? 'Response excerpt: “' + excerpt + '…”' : '') +
              ' Contact your administrator.');
      }).catch(function () {
        _show('Unexpected HTML response (HTTP ' + status + '). Contact your administrator.');
      });
    }

    // --- Non-JSON, non-HTML (e.g. plain text proxy error, empty body) ---
    if (!isJson) {
      return r.text().then(function (body) {
        var excerpt = _sanitize(body);
        _show('Unexpected response type “' + (ct || 'unknown') + '” ' +
              '(HTTP ' + status + ').' +
              (excerpt ? ' Response excerpt: “' + excerpt + '…”' : '') +
              ' Contact your administrator.');
      }).catch(function () {
        _show('Unexpected response (HTTP ' + status + ', type: ' + (ct || 'unknown') + ').' +
              ' Contact your administrator.');
      });
    }

    // --- JSON response (success or structured error) ---
    return r.json().then(function (body) {
      if (!r.ok) {
        // Structured error: {"ok": false, "error": {"code": "...", "message": "..."}}
        var err = body && body.error;
        if (err && typeof err === 'object' && err.code) {
          _show('Error [' + err.code + ']: ' + (err.message || 'An error occurred.'));
        } else if (typeof err === 'string') {
          _show('Error: ' + err);
        } else {
          _show('Request failed (HTTP ' + status + ').');
        }
        return;
      }
      // Success
      _show(JSON.stringify(body, null, 2));
    }).catch(function () {
      // JSON parse failed despite Content-Type: application/json
      _show('The server returned a response that could not be parsed. ' +
            'Contact your administrator.');
    });
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();

    if (_pendingUploads > 0) {
      _show('Please wait — ' + _pendingUploads + ' file(s) are still uploading…');
      if (responseArea) { responseArea.hidden = false; }
      return;
    }

    if (responseArea) { responseArea.hidden = false; }
    _show('Working…');
    var textEl = document.getElementById('ai-request');
    var text = textEl ? textEl.value : '';
    var csrfEl = form.querySelector('input[name=csrfmiddlewaretoken]');
    var csrf = csrfEl ? csrfEl.value : '';
    var payload = { request: text };
    if (_attachmentIds.length > 0) {
      payload.attachment_ids = _attachmentIds.slice();
    }
    fetch(window.location.pathname, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrf,
      },
      body: JSON.stringify(payload),
      credentials: 'same-origin',
    }).then(_handleResponse).catch(function (err) {
      // Network error (no response received — DNS failure, connection refused, etc.)
      _show('Connection error: ' + (err && err.message ? err.message : 'Network request failed.'));
    });
  });
}());
