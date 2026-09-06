/* Authoritative entitlement controls and retry identities for captive browsers. */
(function () {
    'use strict';
    var nativeFetch = window.fetch.bind(window), pending = {}, latest = {};
    function element(tag, text, parent) {
        var node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (parent) parent.appendChild(node);
        return node;
    }
    function duration(seconds) {
        seconds = Math.max(0, Number(seconds) || 0);
        return Math.floor(seconds / 60) + 'm ' + (Math.round((seconds % 60) * 1000000) / 1000000) + 's';
    }
    function date(value) { return value == null ? 'No fixed expiry' : new Date(value * 1000).toLocaleString(); }
    function nonce() {
        var bytes = new Uint32Array(4);
        if (window.crypto && window.crypto.getRandomValues) window.crypto.getRandomValues(bytes);
        else for (var i = 0; i < 4; i++) bytes[i] = Math.random() * 4294967296;
        return Array.from(bytes).map(function (n) { return n.toString(16); }).join('-');
    }
    // Cache only a request fingerprint and random operation ID; never save a PIN/body.
    function fingerprint(value) {
        var a = 2166136261, b = 2246822519;
        for (var i = 0; i < value.length; i++) {
            a = Math.imul(a ^ value.charCodeAt(i), 16777619);
            b = Math.imul(b ^ value.charCodeAt(i), 3266489917);
        }
        return (a >>> 0).toString(16) + (b >>> 0).toString(16);
    }
    function getPending(key) {
        try { return sessionStorage.getItem(key) || pending[key]; } catch (_) { return pending[key]; }
    }
    function setPending(key, value) {
        if (value) pending[key] = value; else delete pending[key];
        try { if (value) sessionStorage.setItem(key, value); else sessionStorage.removeItem(key); } catch (_) {}
    }
    window.fetch = function (input, options) {
        var path = typeof input === 'string' ? input.split('?')[0] : '', key;
        options = Object.assign({}, options || {});
        if (/^\/(admin\/)?api\//.test(path) && (options.method || 'GET').toUpperCase() === 'POST' &&
            (!options.body || typeof options.body === 'string')) {
            try {
                var data = JSON.parse(options.body || '{}');
                if (!data.operation_id) {
                    var identity = Object.assign({}, data); delete identity.pin;
                    key = 'ecofi-pending:' + fingerprint(path + JSON.stringify(identity));
                    data.operation_id = getPending(key) || nonce(); setPending(key, data.operation_id);
                    options.headers = Object.assign({}, options.headers || {}, {'Content-Type':'application/json'});
                    options.body = JSON.stringify(data);
                }
            } catch (_) { /* Multipart uploads and non-JSON bodies retain their original contract. */ }
        }
        return nativeFetch(input, options).then(function (response) {
            return response.clone().json().then(function (data) {
                if (key && response.status < 500 && !data.retryable) setPending(key, null);
                if (path === '/api/vendo/status') {
                    latest = data; setTimeout(function () { renderStatus(data); }, 0);
                }
                if (/^\/api\/member\//.test(path) && data.wallet_seconds != null) {
                    setTimeout(function () {
                        var wallet = document.getElementById('mem-wallet-mins');
                        if (wallet) wallet.textContent = duration(data.wallet_seconds);
                    }, 0);
                }
                return response;
            }, function () { return response; });
        });
    };
    function renderStatus(data) {
        var anchor = document.getElementById('pause-ctrl-box');
        if (!anchor) return;
        var panel = document.getElementById('entitlement-details');
        if (!panel) {
            panel = element('div'); panel.id = 'entitlement-details';
            panel.style.cssText = 'font-size:12px;text-align:left;padding:10px 0;line-height:1.6';
            anchor.parentNode.insertBefore(panel, anchor.nextSibling);
        }
        panel.textContent = '';
        if (data.grant_id) {
            element('div', 'Pauses left: ' + (data.pauses_left == null ? 'Unlimited' : data.pauses_left) +
                ' · Used: ' + data.pause_count_used, panel);
            element('div', 'Valid until: ' + date(data.valid_until_utc), panel);
            if (data.pause_deadline_utc != null) element('div',
                (data.next_event_type === 'resume' ? 'Automatic resume: ' : 'Pause ends: ') + date(data.pause_deadline_utc), panel);
            if (!data.can_pause && !data.is_paused) {
                var reason = data.seconds_until_pausable > 0 ? 'Pause becomes available after ' + duration(data.seconds_until_pausable) + ' of use.' :
                    (data.pauses_left === 0 ? 'This credit has no pauses left.' : 'Pause is unavailable for this credit.');
                element('div', reason, panel);
            }
        }
        if (data.access_error) element('div', data.access_error, panel);
        if (data.worker_healthy === false) element('div', 'Service is recovering. Your remaining credit is preserved.', panel);
        var pause = document.getElementById('btn-pause'), resume = document.getElementById('btn-resume');
        if (pause) pause.disabled = !data.can_pause;
        if (resume) resume.disabled = !!data.admin_paused;
        var badge = document.getElementById('status-badge');
        if (badge && data.applied_state !== 'ACTIVE' && !data.is_paused) badge.textContent = data.remaining_seconds > 0 ? 'WAITING FOR ACCESS' : 'DISCONNECTED';
        (data.grants || []).forEach(function (grant) {
            if (grant.id === data.grant_id || grant.remaining_seconds <= 0) return;
            var row = element('div', duration(grant.remaining_seconds) + ' · ' + grant.state.toLowerCase() + ' · ' + date(grant.valid_until_utc) + ' ', panel);
            if (grant.state !== 'HELD') {
                var button = element('button', 'Use this credit', row); button.type = 'button';
                button.onclick = function () {
                    button.disabled = true;
                    window.fetch('/api/client/switch', {method:'POST',body:JSON.stringify({grant_id:grant.id})})
                        .then(function (r) { return r.json(); }).then(function (result) {
                            if (!result.success) element('div', result.error || 'Unable to switch credit.', panel);
                            if (window.syncPortal) window.syncPortal();
                        }).catch(function () { element('div', 'Connection interrupted. Retry this action.', panel); })
                        .then(function () { button.disabled = false; });
                };
            }
        });
    }
    function policyEditor() {
        if (!/^\/admin\/?$/.test(location.pathname)) return;
        var container = document.querySelector('.content-wrapper');
        if (!container) return;
        nativeFetch('/admin/api/time/policy').then(function (r) { return r.json(); }).then(function (response) {
            if (!response.success) return;
            var policy = response.policy, section = element('details', undefined, container);
            section.className = 'card p-3';
            element('summary', 'Time validity and pause settings', section);
            element('p', 'Changes apply to newly issued credit. Existing credit keeps its purchased terms. Wallet and transfer fragments share the original pause allowance.', section);
            var form = element('form', undefined, section), controls = {};
            function number(key, label, value, nullable) {
                var row = element('label', label + ' ', form); row.style.cssText = 'display:block;margin:10px 0';
                var input = element('input', undefined, row); input.type = 'number'; input.min = '0'; input.step = '1'; input.value = value == null ? '' : value;
                if (nullable) input.placeholder = 'Disabled / unlimited'; else input.required = true;
                controls[key] = input;
            }
            var allowLabel = element('label', 'Allow user pauses ', form), allow = element('input', undefined, allowLabel);
            allow.type = 'checkbox'; allow.checked = !!policy.pause_allowed;
            number('pause_count_max', 'Maximum pauses (blank = unlimited, 0 = none)', policy.pause_count_max, true);
            number('pause_duration_sec', 'Pause duration in seconds (0 = no timeout)', policy.pause_duration_sec, false);
            number('min_balance_sec', 'Minimum remaining seconds to pause (0 = disabled)', policy.min_balance_sec, false);
            number('max_balance_sec', 'Maximum remaining seconds to pause (blank = disabled)', policy.max_balance_sec, true);
            number('global_validity_min', 'Default validity in minutes (blank = no fixed expiry)', policy.global_validity_min, true);
            var actionLabel = element('label', 'When a pause times out: ', form), action = element('select', undefined, actionLabel);
            [['resume','Resume credit'],['expire','Expire remaining credit']].forEach(function (pair) { var option=element('option',pair[1],action); option.value=pair[0]; });
            action.value = policy.pause_timeout_action;
            element('p', 'Optional validity brackets: the first enabled ceiling covering the purchase is used. Otherwise default validity is at least the purchased duration.', form);
            var table = element('table', undefined, form); table.className = 'table table-sm';
            var header = element('tr', undefined, element('thead', undefined, table));
            ['Enabled','Purchased minutes, up to','Validity minutes',''].forEach(function (label) { element('th',label,header); });
            var body = element('tbody', undefined, table);
            function bracket(value) {
                var row = element('tr', undefined, body), enabled = element('input',undefined,element('td',undefined,row));
                enabled.type='checkbox'; enabled.checked=value.enabled !== false;
                ['value','expiration'].forEach(function (key) {
                    var input=element('input',undefined,element('td',undefined,row)); input.type='number';input.min='1';input.step='1';input.required=true;input.value=value[key] || 60;input.style.width='100%';
                });
                var remove=element('button','Remove',element('td',undefined,row)); remove.type='button';remove.onclick=function(){row.remove();};
            }
            (policy.brackets || []).forEach(bracket);
            var add=element('button','Add bracket',form);add.type='button';add.onclick=function(){bracket({});};
            var save=element('button','Save settings for new credit',form);save.type='submit';save.className='btn btn-primary m-2';
            var message=element('p','',form);message.setAttribute('aria-live','polite');
            form.onsubmit=function(event) {
                event.preventDefault();save.disabled=true;
                var data={pause_allowed:allow.checked,pause_timeout_action:action.value,brackets:[]};
                Object.keys(controls).forEach(function(key){data[key]=controls[key].value===''?null:Number(controls[key].value);});
                Array.from(body.rows).forEach(function(row){var values=row.querySelectorAll('input');data.brackets.push({enabled:values[0].checked,value:Number(values[1].value),expiration:Number(values[2].value)});});
                window.fetch('/admin/api/time/policy',{method:'POST',body:JSON.stringify(data)})
                    .then(function(r){return r.json();}).then(function(result){message.textContent=result.success?'Saved. Existing credit retains its terms.':result.error;})
                    .catch(function(){message.textContent='Connection interrupted. Retry saving.';}).then(function(){save.disabled=false;});
            };
            nativeFetch('/admin/api/time/diagnostics').then(function(r){return r.json();}).then(function(d){
                element('p','Accounting: '+(d.ready?'ready':'migration required')+' · Worker: '+(d.worker_healthy?'healthy':'recovering')+' · Held deposit events: '+d.held_deposit_events+' · Balance mismatches: '+d.balance_mismatches.length,section);
            });
        }).catch(function () {});
    }
    document.addEventListener('DOMContentLoaded', policyEditor);
}());
