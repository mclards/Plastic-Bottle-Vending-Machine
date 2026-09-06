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
        seconds = Math.max(0, Math.floor(Number(seconds) || 0));
        var d = Math.floor(seconds / 86400);
        var h = Math.floor((seconds % 86400) / 3600);
        var m = Math.floor((seconds % 3600) / 60);
        var s = seconds % 60;
        if (d > 0) return d + 'd ' + h + 'h';
        if (h > 0) return h + 'h ' + (m > 0 ? m + 'm' : '');
        if (m > 0) return m + 'm ' + (s > 0 ? s + 's' : '');
        return s + 's';
    }
    function date(value) {
        if (value == null) return 'No fixed expiry';
        var d = new Date(value * 1000);
        var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        var m = months[d.getMonth()];
        var day = d.getDate();
        var year = d.getFullYear();
        var hours = d.getHours();
        var mins = d.getMinutes();
        var ampm = hours >= 12 ? 'PM' : 'AM';
        hours = hours % 12;
        if (hours === 0) hours = 12;
        var minsStr = mins < 10 ? '0' + mins : mins;
        return m + ' ' + day + ', ' + year + ' · ' + hours + ':' + minsStr + ' ' + ampm;
    }
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
            anchor.parentNode.insertBefore(panel, anchor.nextSibling);
        }
        panel.textContent = '';
        var hasActiveCredit = !!(data.grant_id && ((data.remaining_seconds > 0) || data.is_paused));
        if (!hasActiveCredit) {
            panel.style.display = 'none';
            return;
        }
        panel.style.display = 'block';
        panel.style.cssText = 'background:rgba(15,23,42,0.65);border:1px solid rgba(255,255,255,0.08);border-radius:9px;padding:10px 12px;margin:8px 0 12px 0;text-align:left;font-size:11.5px;box-shadow:inset 0 1px 0 rgba(255,255,255,0.03);';

        var header = element('div', undefined, panel);
        header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;';

        var title = element('div', undefined, header);
        title.style.cssText = 'font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;color:#94a3b8;display:flex;align-items:center;gap:5px;';
        title.innerHTML = '<i class="fas fa-ticket-alt" style="color:#10b981;font-size:11px;"></i> Session Validity';

        var pauseBadge = element('div', undefined, header);
        if (data.pauses_left == null) {
            pauseBadge.style.cssText = 'background:rgba(16,185,129,0.15);color:#34d399;border:1px solid rgba(16,185,129,0.35);border-radius:12px;padding:2px 8px;font-size:9.5px;font-weight:600;display:inline-flex;align-items:center;gap:4px;';
            pauseBadge.innerHTML = '<i class="fas fa-infinity"></i> Unlimited Pauses';
        } else if (data.pauses_left > 0) {
            pauseBadge.style.cssText = 'background:rgba(245,158,11,0.15);color:#fbbf24;border:1px solid rgba(245,158,11,0.35);border-radius:12px;padding:2px 8px;font-size:9.5px;font-weight:600;display:inline-flex;align-items:center;gap:4px;';
            pauseBadge.innerHTML = '<i class="fas fa-pause-circle"></i> ' + data.pauses_left + ' Pauses Left <span style="color:#94a3b8;font-weight:normal;">(' + data.pause_count_used + ' used)</span>';
        } else {
            pauseBadge.style.cssText = 'background:rgba(239,68,68,0.15);color:#f87171;border:1px solid rgba(239,68,68,0.35);border-radius:12px;padding:2px 8px;font-size:9.5px;font-weight:600;display:inline-flex;align-items:center;gap:4px;';
            pauseBadge.innerHTML = '<i class="fas fa-ban"></i> 0 Pauses Left';
        }

        var validityRow = element('div', undefined, panel);
        validityRow.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding-top:7px;border-top:1px solid rgba(255,255,255,0.06);font-size:11px;color:#cbd5e1;';
        validityRow.innerHTML = '<span style="color:#94a3b8;display:flex;align-items:center;gap:5px;"><i class="far fa-calendar-check" style="color:#38bdf8;"></i> Valid Until:</span><strong style="color:#f1f5f9;font-family:\"SF Mono\",\"Roboto Mono\",monospace;letter-spacing:0.3px;">' + date(data.valid_until_utc) + '</strong>';

        if (data.pause_deadline_utc != null) {
            var deadlineRow = element('div', undefined, panel);
            deadlineRow.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-top:6px;background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.25);border-radius:6px;padding:4px 8px;font-size:10.5px;color:#fbbf24;';
            var deadlineLabel = (data.next_event_type === 'resume' ? 'Auto-resumes: ' : 'Pause ends: ');
            deadlineRow.innerHTML = '<span><i class="fas fa-hourglass-half" style="margin-right:4px;"></i>' + deadlineLabel + '</span><strong style="font-family:\"SF Mono\",\"Roboto Mono\",monospace;">' + date(data.pause_deadline_utc) + '</strong>';
        }

        if (!data.can_pause && !data.is_paused) {
            var reason = data.seconds_until_pausable > 0 ? 'Pause available in ' + duration(data.seconds_until_pausable) + ' of active use.' :
                (data.pauses_left === 0 ? 'All pause allowances for this credit have been used.' : 'Pause is unavailable for this credit.');
            var reasonRow = element('div', undefined, panel);
            reasonRow.style.cssText = 'margin-top:6px;font-size:10.5px;color:#94a3b8;display:flex;align-items:flex-start;gap:5px;line-height:1.35;';
            reasonRow.innerHTML = '<i class="fas fa-info-circle" style="color:#64748b;margin-top:2px;"></i><span>' + reason + '</span>';
        }

        if (data.access_error) {
            var errBox = element('div', undefined, panel);
            errBox.style.cssText = 'margin-top:6px;font-size:10.5px;color:#fca5a5;background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);border-radius:6px;padding:5px 8px;display:flex;align-items:center;gap:5px;';
            var errIcon = element('i', undefined, errBox); errIcon.className = 'fas fa-exclamation-triangle';
            var errSpan = element('span', data.access_error, errBox);
        }
        if (data.worker_healthy === false) {
            var warnBox = element('div', undefined, panel);
            warnBox.style.cssText = 'margin-top:6px;font-size:10.5px;color:#fde68a;background:rgba(245,158,11,0.15);border:1px solid rgba(245,158,11,0.3);border-radius:6px;padding:5px 8px;display:flex;align-items:center;gap:5px;';
            var warnIcon = element('i', undefined, warnBox); warnIcon.className = 'fas fa-sync-alt fa-spin';
            var warnSpan = element('span', 'Service is recovering. Your remaining credit is preserved.', warnBox);
        }

        var pause = document.getElementById('btn-pause'), resume = document.getElementById('btn-resume');
        if (pause) pause.disabled = !data.can_pause;
        if (resume) resume.disabled = !!data.admin_paused;
        var badge = document.getElementById('status-badge');
        if (badge && data.applied_state !== 'ACTIVE' && !data.is_paused) badge.textContent = data.remaining_seconds > 0 ? 'WAITING FOR ACCESS' : 'DISCONNECTED';

        var otherGrants = (data.grants || []).filter(function(g) { return g.id !== data.grant_id && g.remaining_seconds > 0; });
        if (otherGrants.length > 0) {
            var grantsSection = element('div', undefined, panel);
            grantsSection.style.cssText = 'margin-top:8px;padding-top:7px;border-top:1px solid rgba(255,255,255,0.06);';
            var grantTitle = element('div', 'Other Available Credits:', grantsSection);
            grantTitle.style.cssText = 'font-size:10px;text-transform:uppercase;color:#94a3b8;font-weight:600;margin-bottom:5px;';

            otherGrants.forEach(function(grant) {
                var row = element('div', undefined, grantsSection);
                row.style.cssText = 'display:flex;justify-content:space-between;align-items:center;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:6px;padding:5px 8px;margin-bottom:4px;font-size:11px;color:#cbd5e1;';
                element('span', duration(grant.remaining_seconds) + ' (' + grant.state.toLowerCase() + ')', row);
                if (grant.state !== 'HELD') {
                    var button = element('button', 'Use Credit', row);
                    button.type = 'button';
                    button.className = 'btn-tactile btn-tactile-green';
                    button.style.cssText = 'height:24px;font-size:10.5px;padding:0 8px;';
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
    }
    function policyEditor() {
        if (!/^\/admin\/?$/.test(location.pathname)) return;
        var container = document.getElementById('sec-rates') || document.querySelector('.content-wrapper');
        if (!container) return;
        if (document.getElementById('time-policy-card')) return;

        nativeFetch('/admin/api/time/policy').then(function (r) { return r.json(); }).then(function (response) {
            if (!response.success) return;
            var policy = response.policy;

            var defaultBrackets = [
                { value: 30, expiration: 1440, enabled: true },      // Up to 30m: 1 Day
                { value: 60, expiration: 2880, enabled: true },      // Up to 1h: 2 Days
                { value: 180, expiration: 4320, enabled: true },     // Up to 3h: 3 Days
                { value: 360, expiration: 10080, enabled: true },    // Up to 6h: 7 Days
                { value: 720, expiration: 21600, enabled: true },    // Up to 12h: 15 Days
                { value: 1440, expiration: 43200, enabled: true },   // Up to 24h: 30 Days (1mo)
                { value: 4320, expiration: 86400, enabled: true },   // Up to 3d: 60 Days (2mo)
                { value: 10080, expiration: 129600, enabled: true }, // Up to 7d: 90 Days (3mo)
                { value: 43200, expiration: 259200, enabled: true }  // Up to 30d: 180 Days (6mo)
            ];
            var bracketsList = (policy.brackets && policy.brackets.length > 0) ? policy.brackets : defaultBrackets;

            function formatDurationBadge(mins) {
                if (!mins || mins <= 0) return '';
                if (mins < 60) return mins + 'm';
                if (mins < 1440) {
                    var h = Math.round(mins / 60 * 10) / 10;
                    return (h % 1 === 0 ? Math.round(h) : h) + 'h';
                }
                var d = Math.round(mins / 1440);
                if (d < 30) return d + 'd';
                var mo = Math.round(d / 30 * 10) / 10;
                return (mo % 1 === 0 ? Math.round(mo) : mo) + 'mo';
            }

            var card = element('div', undefined, container);
            card.id = 'time-policy-card';
            card.className = 'card mb-3 mt-3';
            card.style.cssText = 'background:#1e293b; border:1px solid rgba(255,255,255,0.08); border-radius:8px; overflow:hidden;';

            // Clean Minimal Header (No unnecessary lines or badges)
            var cardHeader = element('div', undefined, card);
            cardHeader.className = 'card-header py-2 px-3';
            cardHeader.style.cssText = 'background:#0f172a; border-bottom:1px solid rgba(255,255,255,0.06);';
            cardHeader.innerHTML = '<h3 class="card-title font-weight-bold text-light mb-0" style="font-size:13px;">' +
                '<i class="fas fa-clock text-primary mr-2"></i>Time Validity & Pause Settings</h3>';

            var cardBody = element('div', undefined, card);
            cardBody.className = 'card-body p-2 p-sm-3';

            var form = element('form', undefined, cardBody);
            var controls = {};

            // Row 1: Inline Controls Bar (Switch + Action) - Mobile Adaptive
            var topBar = element('div', undefined, form);
            topBar.className = 'd-flex flex-wrap justify-content-between align-items-center mb-2';
            topBar.style.cssText = 'gap: 8px;';

            var pauseSwitchBox = element('div', undefined, topBar);
            pauseSwitchBox.className = 'custom-control custom-switch';
            pauseSwitchBox.innerHTML = '<input type="checkbox" class="custom-control-input" id="policy-allow-pause">' +
                '<label class="custom-control-label font-weight-bold text-light small" for="policy-allow-pause" style="cursor:pointer; font-size:11.5px;">' +
                'Allow Client Pauses</label>';
            var allowInput = pauseSwitchBox.querySelector('#policy-allow-pause');
            allowInput.checked = !!policy.pause_allowed;

            var actionBox = element('div', undefined, topBar);
            actionBox.className = 'd-flex align-items-center';
            actionBox.style.cssText = 'gap: 6px;';
            actionBox.innerHTML = '<span class="text-muted small font-weight-bold" style="font-size:11px; white-space:nowrap;">Timeout Action:</span>' +
                '<select class="custom-select custom-select-sm" id="policy-timeout-action" style="height:28px; width:auto; min-width:145px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 8px;">' +
                '<option value="resume">Resume (Auto-Unpause)</option><option value="expire">Expire Remaining Credit</option></select>';
            var actionSelect = actionBox.querySelector('#policy-timeout-action');
            actionSelect.value = policy.pause_timeout_action || 'resume';

            // Row 2: 5 Responsive Input Fields (CSS Grid: 2 cols on mobile, 5 on desktop)
            var fieldsGrid = element('div', undefined, form);
            fieldsGrid.className = 'mb-2';
            fieldsGrid.style.cssText = 'display:grid; grid-template-columns:repeat(auto-fit, minmax(130px, 1fr)); gap:8px;';

            function addCompactField(key, label, value, nullable, placeholder, tooltip) {
                var formGroup = element('div', undefined, fieldsGrid);
                formGroup.className = 'form-group mb-0';

                var lbl = element('label', undefined, formGroup);
                lbl.className = 'font-weight-bold small mb-1 d-block text-truncate';
                lbl.style.cssText = 'font-size: 11px; color: #94a3b8; letter-spacing: 0.2px;';
                lbl.title = tooltip;
                lbl.innerHTML = label;

                var input = element('input', undefined, formGroup);
                input.type = 'number';
                input.min = '0';
                input.step = '1';
                input.title = tooltip;
                input.placeholder = placeholder || '';
                input.className = 'form-control form-control-sm';
                input.style.cssText = 'height: 28px; width: 100%; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size: 12px; padding: 2px 8px; border-radius: 4px; box-sizing: border-box;';
                input.value = value == null ? '' : value;
                if (!nullable) input.required = true;
                controls[key] = input;
            }

            addCompactField('pause_count_max', 'Max Pauses', policy.pause_count_max, true, 'Unlimited', 'Maximum pauses per credit session (0 = none, Blank = unlimited)');
            addCompactField('pause_duration_sec', 'Pause Timeout (s)', policy.pause_duration_sec, false, '3600 (1 hour)', 'Max duration of one pause in seconds before timeout triggers');
            addCompactField('global_validity_min', 'Default Validity (m)', policy.global_validity_min, true, '1440 (24 hrs)', 'Global fallback validity in minutes if no brackets match');
            addCompactField('min_balance_sec', 'Min Pause Bal (s)', policy.min_balance_sec, false, '0 (No Min)', 'Minimum remaining seconds required to pause');
            addCompactField('max_balance_sec', 'Max Pause Bal (s)', policy.max_balance_sec, true, 'Disabled', 'Maximum remaining balance allowed to pause (optional limit)');

            // Section: Validity Brackets Table
            var bracketsContainer = element('div', undefined, form);
            bracketsContainer.className = 'mt-2 pt-1';

            var bracketsHeader = element('div', undefined, bracketsContainer);
            bracketsHeader.className = 'd-flex justify-content-between align-items-center mb-1';
            bracketsHeader.innerHTML = '<h6 class="font-weight-bold text-light mb-0" style="font-size:11.5px;">' +
                '<i class="fas fa-layer-group text-info mr-1"></i>Tiered Validity Brackets</h6>';

            var addBtn = element('button', undefined, bracketsHeader);
            addBtn.type = 'button';
            addBtn.className = 'btn btn-xs btn-outline-info font-weight-bold px-2 py-0';
            addBtn.style.cssText = 'height: 23px; font-size: 11px;';
            addBtn.innerHTML = '<i class="fas fa-plus mr-1"></i>Add Bracket';

            var tableWrapper = element('div', undefined, bracketsContainer);
            tableWrapper.className = 'table-responsive';
            tableWrapper.style.cssText = 'max-height: 185px; overflow-y: auto; overflow-x: auto; -webkit-overflow-scrolling: touch; border: 1px solid rgba(255,255,255,0.06); border-radius: 5px;';

            var table = element('table', undefined, tableWrapper);
            table.className = 'table table-sm text-light mb-0';
            table.style.cssText = 'background: rgba(15,23,42,0.4); font-size: 11.5px;';
            table.innerHTML = '<thead style="background:#0f172a; color:#94a3b8; font-size:10.5px;">' +
                '<tr>' +
                '<th style="width:45px;" class="text-center py-1">Active</th>' +
                '<th class="py-1">Earned Time (Up To)</th>' +
                '<th class="py-1">Validity (Expires After)</th>' +
                '<th style="width:36px;" class="text-center py-1"></th>' +
                '</tr></thead>';
            var body = element('tbody', undefined, table);

            function addBracketRow(value) {
                var row = element('tr', undefined, body);
                row.style.cssText = 'border-top: 1px solid rgba(255,255,255,0.04);';

                var tdEnabled = element('td', undefined, row);
                tdEnabled.className = 'text-center align-middle py-1';
                var enCheck = element('input', undefined, tdEnabled);
                enCheck.type = 'checkbox';
                enCheck.checked = value.enabled !== false;
                enCheck.title = 'Enable or disable this bracket';

                var tdVal = element('td', undefined, row);
                tdVal.className = 'align-middle py-1';
                var valBox = element('div', undefined, tdVal);
                valBox.className = 'd-flex align-items-center';
                var inputVal = element('input', undefined, valBox);
                inputVal.type = 'number';
                inputVal.min = '1';
                inputVal.step = '1';
                inputVal.required = true;
                inputVal.value = value.value || 60;
                inputVal.className = 'form-control form-control-sm mr-1';
                inputVal.style.cssText = 'height:25px; width:70px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 5px; border-radius:3px;';
                var badgeVal = element('span', undefined, valBox);
                badgeVal.className = 'badge badge-dark text-info border border-secondary px-1 py-0';
                badgeVal.style.cssText = 'font-size:10px; font-weight:600; white-space:nowrap;';
                function updateValBadge() { badgeVal.innerText = formatDurationBadge(Number(inputVal.value)); }
                inputVal.oninput = updateValBadge;
                updateValBadge();

                var tdExp = element('td', undefined, row);
                tdExp.className = 'align-middle py-1';
                var expBox = element('div', undefined, tdExp);
                expBox.className = 'd-flex align-items-center';
                var inputExp = element('input', undefined, expBox);
                inputExp.type = 'number';
                inputExp.min = '1';
                inputExp.step = '1';
                inputExp.required = true;
                inputExp.value = value.expiration || 1440;
                inputExp.className = 'form-control form-control-sm mr-1';
                inputExp.style.cssText = 'height:25px; width:80px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 5px; border-radius:3px;';
                var badgeExp = element('span', undefined, expBox);
                badgeExp.className = 'badge badge-dark text-success border border-secondary px-1 py-0';
                badgeExp.style.cssText = 'font-size:10px; font-weight:600; white-space:nowrap;';
                function updateExpBadge() { badgeExp.innerText = formatDurationBadge(Number(inputExp.value)); }
                inputExp.oninput = updateExpBadge;
                updateExpBadge();

                var tdAction = element('td', undefined, row);
                tdAction.className = 'text-center align-middle py-1';
                var removeBtn = element('button', undefined, tdAction);
                removeBtn.type = 'button';
                removeBtn.className = 'btn btn-xs btn-outline-danger py-0 px-1';
                removeBtn.style.cssText = 'line-height:1; font-size:10px;';
                removeBtn.innerHTML = '<i class="fas fa-times"></i>';
                removeBtn.onclick = function () { row.remove(); };
            }

            bracketsList.forEach(addBracketRow);
            addBtn.onclick = function () { addBracketRow({ value: 120, expiration: 2880, enabled: true }); };

            // Combined Clean Footer: Save Button + Status Pills
            var footer = element('div', undefined, card);
            footer.className = 'card-footer py-2 px-3 d-flex flex-wrap justify-content-between align-items-center';
            footer.style.cssText = 'background:#0f172a; border-top:1px solid rgba(255,255,255,0.06); gap:8px;';

            var leftAction = element('div', undefined, footer);
            leftAction.className = 'd-flex align-items-center';

            var saveBtn = element('button', undefined, leftAction);
            saveBtn.type = 'submit';
            saveBtn.className = 'btn btn-sm btn-primary font-weight-bold px-3 py-1 mr-2';
            saveBtn.style.cssText = 'font-size:12px; height:29px; white-space:nowrap;';
            saveBtn.innerHTML = '<i class="fas fa-save mr-1"></i> Save Policy';

            var message = element('span', '', leftAction);
            message.className = 'small font-weight-bold';
            message.setAttribute('aria-live', 'polite');

            var diagStrip = element('div', undefined, footer);
            diagStrip.className = 'd-flex flex-wrap align-items-center small text-muted';
            diagStrip.style.cssText = 'gap: 8px; font-size: 10.5px;';

            form.onsubmit = function (event) {
                event.preventDefault();
                saveBtn.disabled = true;
                message.className = 'small font-weight-bold text-info';
                message.textContent = 'Saving...';

                var data = {
                    pause_allowed: allowInput.checked,
                    pause_timeout_action: actionSelect.value,
                    brackets: []
                };
                Object.keys(controls).forEach(function (key) {
                    data[key] = controls[key].value === '' ? null : Number(controls[key].value);
                });
                Array.from(body.rows).forEach(function (row) {
                    var inputs = row.querySelectorAll('input');
                    data.brackets.push({
                        enabled: inputs[0].checked,
                        value: Number(inputs[1].value),
                        expiration: Number(inputs[2].value)
                    });
                });

                window.fetch('/admin/api/time/policy', { method: 'POST', body: JSON.stringify(data) })
                    .then(function (r) { return r.json(); })
                    .then(function (result) {
                        if (result.success) {
                            message.className = 'small font-weight-bold text-success';
                            message.innerHTML = '<i class="fas fa-check-circle mr-1"></i> Saved.';
                            setTimeout(function () { if (message.textContent.indexOf('Saved') !== -1) message.textContent = ''; }, 3000);
                        } else {
                            message.className = 'small font-weight-bold text-danger';
                            message.innerHTML = '<i class="fas fa-exclamation-triangle mr-1"></i> ' + (result.error || 'Error');
                        }
                    })
                    .catch(function () {
                        message.className = 'small font-weight-bold text-danger';
                        message.innerHTML = '<i class="fas fa-exclamation-triangle mr-1"></i> Network error.';
                    })
                    .then(function () { saveBtn.disabled = false; });
            };

            nativeFetch('/admin/api/time/diagnostics').then(function (r) { return r.json(); }).then(function (d) {
                var accBadge = d.ready ? '<span class="badge badge-success px-1">Ready</span>' : '<span class="badge badge-warning px-1">Migrate</span>';
                var workerBadge = d.worker_healthy ? '<span class="badge badge-success px-1">Healthy</span>' : '<span class="badge badge-danger px-1">Recovering</span>';
                var mismatchBadge = (d.balance_mismatches && d.balance_mismatches.length > 0) ?
                    '<span class="badge badge-danger px-1">' + d.balance_mismatches.length + '</span>' :
                    '<span class="badge badge-success px-1">0</span>';
                var heldBadge = '<span class="badge badge-secondary px-1">' + (d.held_deposit_events || 0) + '</span>';

                diagStrip.innerHTML = '<span>Accounting: ' + accBadge + '</span>' +
                    '<span>Worker: ' + workerBadge + '</span>' +
                    '<span>Held: ' + heldBadge + '</span>' +
                    '<span>Mismatches: ' + mismatchBadge + '</span>';
            }).catch(function () {});
        }).catch(function () {});
    }
    document.addEventListener('DOMContentLoaded', policyEditor);
}());
