// --- CHUTE SEQUENCE & SYSTEM LOGGER CLIENT ENGINE ---
var sequencePollTimer = null;
var systemLogPollTimer = null;
var lastKnownLogId = 0;
var cachedSystemLogs = [];

function startSequencePolling() {
    var globalFooter = document.getElementById('esp-global-card-footer');
    if (globalFooter) {
        globalFooter.classList.remove('d-flex');
        globalFooter.classList.add('d-none');
    }
    if (sequencePollTimer) return;
    pollChuteSequence();
    sequencePollTimer = setInterval(pollChuteSequence, 1000);
}

function stopSequencePolling() {
    if (sequencePollTimer) {
        clearInterval(sequencePollTimer);
        sequencePollTimer = null;
    }
}

function pollChuteSequence() {
    fetch('/admin/api/esp32/sequence_monitor')
        .then(function(r) { return r.json(); })
        .then(function(res) {
            if (res && res.success && res.data) {
                updateChuteSequenceUI(res.data);
            }
        })
        .catch(function(err) {
            console.warn('Sequence poll error:', err);
        });
}

function updateChuteSequenceUI(data) {
    if (!data) return;
    var status = data.status || 'IDLE';
    var currentStage = data.current_stage || 0;
    var elapsedSec = ((data.total_elapsed_ms || 0) / 1000).toFixed(2);

    var statBadge = document.getElementById('seq-status-badge');
    if (statBadge) {
        statBadge.textContent = status;
        statBadge.className = 'badge font-weight-bold ml-2 ' + (
            status === 'PASSED' ? 'badge-success' :
            status === 'SCANNING' ? 'badge-warning' :
            status === 'REJECTED' ? 'badge-danger' :
            status === 'TIMEOUT' ? 'badge-warning' : 'badge-secondary'
        );
        statBadge.style.display = 'inline-block';
        statBadge.style.minWidth = '74px';
        statBadge.style.textAlign = 'center';
        statBadge.style.fontSize = '11.5px';
        statBadge.style.padding = '3.5px 8px';
        statBadge.style.borderRadius = '4px';
        statBadge.style.letterSpacing = '0.5px';
    }

    var sessEl = document.getElementById('seq-session-id');
    if (sessEl) sessEl.textContent = data.active_session_id || '--';

    var timerEl = document.getElementById('seq-elapsed-timer');
    if (timerEl) timerEl.textContent = elapsedSec + ' s';

    var pBar = document.getElementById('seq-progress-bar');
    if (pBar) {
        var pct = status === 'PASSED' ? 100 : Math.min(100, Math.round((currentStage / 6) * 100));
        pBar.style.width = pct + '%';
        pBar.className = 'progress-bar progress-bar-striped progress-bar-animated ' + (
            status === 'PASSED' ? 'bg-success' : (status === 'REJECTED' ? 'bg-danger' : 'bg-warning')
        );
    }

    var stages = data.stages || {};
    // Stage keys ordered authoritatively by v2.3.18 firmware sequence:
    // S1: Gate -> S2: Top IR Intake -> S3: HX711 Scale -> S4: AS7263 NIR -> S5: Flap Servo -> S6: Bottom IR Drop
    var stageKeys = ['1_gate', '2_intake', '3_scale', '4_nir', '5_exit', '6_drop'];

    stageKeys.forEach(function(key, idx) {
        var num = idx + 1;
        var st = stages[key] || {};
        var nodeEl = document.getElementById('seq-node-' + num);
        var arrowEl = document.getElementById('seq-arr-' + num);
        var latEl = document.getElementById('seq-s' + num + '-lat');
        var stateEl = document.getElementById('seq-s' + num + '-state');
        var iconEl = document.getElementById('seq-s' + num + '-icon');
        var rowLat = document.getElementById('row-s' + num + '-lat');
        var rowStat = document.getElementById('row-s' + num + '-stat');

        var stState = st.status || 'idle';
        var isAct = (stState === 'active');
        var isPass = (stState === 'passed');
        var isFail = (stState === 'failed');

        if (nodeEl) {
            var bg = isPass ? 'rgba(16, 185, 129, 0.18)' : (isAct ? 'rgba(245, 158, 11, 0.2)' : (isFail ? 'rgba(239, 68, 68, 0.2)' : 'var(--eco-card-sub)'));
            var bColor = isPass ? '#10b981' : (isAct ? '#f59e0b' : (isFail ? '#ef4444' : 'var(--eco-border)'));
            nodeEl.style.background = bg;
            nodeEl.style.borderColor = bColor;
            if (isAct) {
                nodeEl.style.boxShadow = '0 0 10px rgba(245, 158, 11, 0.35)';
            } else if (isPass) {
                nodeEl.style.boxShadow = '0 0 8px rgba(16, 185, 129, 0.25)';
            } else {
                nodeEl.style.boxShadow = 'none';
            }
        }

        if (arrowEl) {
            arrowEl.className = 'fas fa-chevron-right ' + (isPass ? 'text-success' : (isAct ? 'text-warning' : 'text-muted'));
        }

        if (iconEl) {
            iconEl.className = iconEl.className.replace(/text-[a-z]+/, '') + ' ' + (isPass ? 'text-success' : (isAct ? 'text-warning' : (isFail ? 'text-danger' : 'text-muted')));
        }

        var latVal = st.elapsed_ms != null ? st.elapsed_ms : st.latency_ms;
        var latText = (latVal != null && latVal > 0) ? (latVal + 'ms') : '--';
        if (latEl) latEl.textContent = latText;
        if (rowLat) rowLat.textContent = latText;

        if (stateEl) {
            stateEl.textContent = st.detail || (isPass ? 'PASSED' : (isAct ? 'MEASURING' : (isFail ? 'REJECT' : 'WAIT')));
            stateEl.className = 'badge ' + (isPass ? 'badge-success' : (isAct ? 'badge-warning' : (isFail ? 'badge-danger' : 'badge-secondary')));
            stateEl.style.display = 'block';
            stateEl.style.width = '100%';
            stateEl.style.textAlign = 'center';
            stateEl.style.padding = '3.5px 4px';
            stateEl.style.fontSize = '12px';
            stateEl.style.fontWeight = '700';
            stateEl.style.borderRadius = '4px';
            stateEl.style.whiteSpace = 'nowrap';
            stateEl.style.overflow = 'hidden';
            stateEl.style.textOverflow = 'ellipsis';
        }

        if (rowStat) {
            var bClass = isPass ? 'badge-success' : (isAct ? 'badge-warning' : (isFail ? 'badge-danger' : 'badge-secondary'));
            var bText = isPass ? 'OK' : (isAct ? 'RUN' : (isFail ? 'FAIL' : 'WAIT'));
            rowStat.innerHTML = '<span class="badge ' + bClass + '" style="display:inline-block;width:68px;text-align:center;font-size:11.5px;font-weight:700;padding:3px 0;border-radius:4px;letter-spacing:0.5px;">' + bText + '</span>';
        }
    });

    if (stages['3_scale'] && stages['3_scale'].detail) {
        var wEl = document.getElementById('seq-s3-weight');
        if (wEl) wEl.textContent = stages['3_scale'].detail;
    }
    if (stages['4_nir'] && stages['4_nir'].detail) {
        var calwEl = document.getElementById('seq-s4-calw');
        if (calwEl) calwEl.textContent = stages['4_nir'].detail;
    }

    var rowTotLat = document.getElementById('row-tot-lat');
    if (rowTotLat) rowTotLat.textContent = (data.total_elapsed_ms || 0) + 'ms';
    var badgeTot = document.getElementById('badge-tot-stat');
    if (badgeTot) {
        var totText = status === 'PASSED' ? 'PASSED' : (status === 'REJECTED' ? 'REJECT' : (status === 'SCANNING' ? 'RUN' : (status || 'IDLE')));
        var totClass = status === 'PASSED' ? 'badge-success' : (status === 'REJECTED' ? 'badge-danger' : (status === 'SCANNING' ? 'badge-warning' : 'badge-secondary'));
        badgeTot.textContent = totText;
        badgeTot.className = 'badge ' + totClass;
        badgeTot.style.display = 'inline-block';
        badgeTot.style.width = '68px';
        badgeTot.style.textAlign = 'center';
        badgeTot.style.fontSize = '11.5px';
        badgeTot.style.fontWeight = '700';
        badgeTot.style.padding = '3px 0';
        badgeTot.style.borderRadius = '4px';
        badgeTot.style.letterSpacing = '0.5px';
    }

    var evList = data.events || data.recent_logs;
    if (evList && evList.length > 0) {
        renderChuteLogTerminal(evList);
    } else {
        syncChuteTerminalHeight();
    }
}

function renderChuteLogTerminal(events) {
    var term = document.getElementById('seq-log-terminal');
    if (!term) return;

    var lines = events.map(function(ev) {
        var ts = ev.timestamp || ev.ts || '';
        var stage = ev.stage || ev.category || 'SYS';
        var msg = ev.message || ev.msg || '';

        var tsHtml = '<span class="text-muted font-mono mr-1" style="font-weight:600;">[' + escapeHtml(ts) + ']</span>';

        var badgeStyle = 'background:#64748b;color:#fff;';
        if (stage === 'CHUTE') badgeStyle = 'background:#0284c7;color:#fff;';
        else if (stage === 'SENSOR') badgeStyle = 'background:#7c3aed;color:#fff;';
        else if (stage === 'ACTUATOR') badgeStyle = 'background:#d97706;color:#fff;';
        else if (stage === 'SYSTEM') badgeStyle = 'background:#475569;color:#fff;';

        var stageHtml = '<span class="badge px-2 py-0 font-mono" style="' + badgeStyle + 'font-size:11px;font-weight:700;margin-right:4px;border-radius:3px;">' + escapeHtml(stage) + '</span>';

        var msgClass = '';
        var msgStyle = 'color:var(--eco-text-main);';
        if (msg.indexOf('REJECT') !== -1 || msg.indexOf('FAIL') !== -1 || msg.indexOf('TIMEOUT') !== -1) {
            msgClass = 'text-danger font-weight-bold';
            msgStyle = '';
        } else if (msg.indexOf('MEASURE') !== -1 || msg.indexOf('SCAN') !== -1 || msg.indexOf('Cal-W=') !== -1 || msg.indexOf('Mass:') !== -1) {
            msgClass = 'text-warning font-weight-bold';
            msgStyle = '';
        } else if (msg.indexOf('CONFIRMED') !== -1 || msg.indexOf('ACCEPTED') !== -1 || msg.indexOf('saved') !== -1 || msg.indexOf('Authentic') !== -1) {
            msgClass = 'text-success font-weight-bold';
            msgStyle = '';
        } else if (msg.indexOf('OPENED') !== -1 || msg.indexOf('TRIGGERED') !== -1) {
            msgClass = 'text-primary font-weight-bold';
            msgStyle = '';
        }

        return tsHtml + ' ' + stageHtml + ' <span class="' + msgClass + '" style="' + msgStyle + '">' + escapeHtml(msg) + '</span>';
    });

    term.innerHTML = lines.join('\n');
    term.scrollTop = term.scrollHeight;
    syncChuteTerminalHeight();
}

function simulateChuteSequence(isPet) {
    fetch('/admin/api/esp32/sequence/simulate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_pet: isPet, weight: isPet ? 34.5 : 9.2, cal_w: isPet ? 48.2 : 12.0 })
    })
    .then(function(r) { return r.json(); })
    .then(function() {
        startSequencePolling();
    })
    .catch(function(err) {
        console.error('Simulate error:', err);
    });
}

function resetChuteSequence() {
    fetch('/admin/api/esp32/sequence/reset', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(res) {
            if (res && res.data) updateChuteSequenceUI(res.data);
        });
}

// --- SYSTEM EVENT LOGGER ENGINE ---
function startSystemLogPolling() {
    if (systemLogPollTimer) return;
    fetchSystemLogs(true);
    systemLogPollTimer = setInterval(function() {
        fetchSystemLogs(false);
    }, 2000);
}

function stopSystemLogPolling() {
    if (systemLogPollTimer) {
        clearInterval(systemLogPollTimer);
        systemLogPollTimer = null;
    }
}

function fetchSystemLogs(forceReload) {
    var sinceId = forceReload ? 0 : lastKnownLogId;
    var cat = document.getElementById('sys-log-cat') ? document.getElementById('sys-log-cat').value : 'ALL';
    var lvl = document.getElementById('sys-log-lvl') ? document.getElementById('sys-log-lvl').value : 'ALL';
    var url = '/admin/api/system/logs?since_id=' + sinceId + '&limit=250';
    if (cat && cat !== 'ALL') url += '&category=' + encodeURIComponent(cat);
    if (lvl && lvl !== 'ALL') url += '&level=' + encodeURIComponent(lvl);

    fetch(url)
        .then(function(r) { return r.json(); })
        .then(function(res) {
            if (res && res.success && res.entries) {
                if (forceReload) {
                    cachedSystemLogs = res.entries;
                } else if (res.entries.length > 0) {
                    cachedSystemLogs = cachedSystemLogs.concat(res.entries);
                    if (cachedSystemLogs.length > 800) {
                        cachedSystemLogs = cachedSystemLogs.slice(cachedSystemLogs.length - 800);
                    }
                }
                if (res.last_id) lastKnownLogId = res.last_id;
                renderSystemLogs(cachedSystemLogs);
            }
        })
        .catch(function(err) {
            console.warn('System log fetch error:', err);
        });
}

function renderSystemLogs(entries) {
    var term = document.getElementById('sys-log-terminal');
    if (!term) return;
    var searchEl = document.getElementById('sys-log-search');
    var query = searchEl ? searchEl.value.trim().toLowerCase() : '';

    var filtered = entries;
    if (query) {
        filtered = entries.filter(function(e) {
            var fullText = (e.raw || (e.message || '') + ' ' + (e.category || '')).toLowerCase();
            return fullText.indexOf(query) !== -1;
        });
    }

    if (filtered.length === 0) {
        term.innerHTML = '<span class="text-muted">No matching system event log entries.</span>';
        return;
    }

    var lines = filtered.map(function(e) {
        var ts = e.timestamp || e.ts || '';
        var lvl = e.level || e.lvl || 'INFO';
        var cat = e.category || e.cat || 'SYS';
        var msg = e.message || e.msg || '';

        var lvlBadge = 'badge-secondary';
        if (lvl === 'TRIGGER') lvlBadge = 'badge-primary';
        else if (lvl === 'MEASURE') lvlBadge = 'badge-warning';
        else if (lvl === 'SUCCESS') lvlBadge = 'badge-success';
        else if (lvl === 'WARN') lvlBadge = 'badge-warning';
        else if (lvl === 'ERROR') lvlBadge = 'badge-danger';

        var catBadge = 'background:#475569;color:#fff;';
        if (cat === 'CHUTE') catBadge = 'background:#0284c7;color:#fff;';
        else if (cat === 'SENSOR') catBadge = 'background:#7c3aed;color:#fff;';
        else if (cat === 'ACTUATOR') catBadge = 'background:#d97706;color:#fff;';

        return '<span class="text-muted font-mono mr-1" style="font-weight:600;">[' + escapeHtml(ts) + ']</span> ' +
               '<span class="badge ' + lvlBadge + ' px-1 font-mono" style="font-size:9.5px;">' + escapeHtml(lvl) + '</span> ' +
               '<span class="badge px-1 font-mono" style="' + catBadge + 'font-size:9.5px;">' + escapeHtml(cat) + '</span> ' +
               '<span style="color:var(--eco-text-main);">' + escapeHtml(msg) + '</span>';
    });

    term.innerHTML = lines.join('\n');
    term.scrollTop = term.scrollHeight;
}

function filterSystemLogsLocally() {
    renderSystemLogs(cachedSystemLogs);
}

function copySequenceTerminalLogs() {
    var term = document.getElementById('seq-log-terminal');
    if (!term) return;
    var text = term.innerText || term.textContent;
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function() {
            Swal.fire({ toast: true, position: 'top-end', icon: 'success', title: 'Chute log copied', timer: 1500, showConfirmButton: false });
        });
    }
}

function clearSystemLogs() {
    Swal.fire({
        title: 'Clear System Event Logs?',
        text: 'This will truncate /opt/ecofi/system_events.log and reset live sequence history.',
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#ef4444',
        cancelButtonColor: '#64748b',
        confirmButtonText: 'Yes, Clear Logs'
    }).then(function(result) {
        if (result.isConfirmed) {
            fetch('/admin/api/system/logs/clear', { method: 'POST' })
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    cachedSystemLogs = [];
                    lastKnownLogId = 0;
                    var term1 = document.getElementById('seq-log-terminal');
                    if (term1) term1.innerHTML = '<span class="text-muted">[System log cleared]</span>';
                    var term2 = document.getElementById('sys-log-terminal');
                    if (term2) term2.innerHTML = '<span class="text-muted">[System log cleared]</span>';
                    Swal.fire('Logs Cleared', d.message || 'Log file cleared successfully.', 'success');
                });
        }
    });
}

function escapeHtml(text) {
    if (!text) return '';
    return text.toString()
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function syncChuteTerminalHeight() {
    var leftCard = document.getElementById('card-latency-budget');
    var rightCard = document.getElementById('card-chute-terminal');
    var term = document.getElementById('seq-log-terminal');
    if (!leftCard || !rightCard || !term) return;

    if (window.innerWidth >= 992) {
        var leftHeight = leftCard.offsetHeight;
        if (leftHeight > 0) {
            rightCard.style.height = leftHeight + 'px';
            var headerEl = rightCard.querySelector('.card-header');
            var headerHeight = headerEl ? headerEl.offsetHeight : 32;
            var availableTermHeight = leftHeight - headerHeight - 16;
            if (availableTermHeight > 60) {
                term.style.height = availableTermHeight + 'px';
                term.style.maxHeight = availableTermHeight + 'px';
            }
        }
    } else {
        rightCard.style.height = '';
        term.style.height = '180px';
        term.style.maxHeight = '180px';
    }
}

if (typeof window !== 'undefined') {
    window.addEventListener('resize', syncChuteTerminalHeight);
    window.addEventListener('load', function() {
        setTimeout(syncChuteTerminalHeight, 100);
        setTimeout(syncChuteTerminalHeight, 350);
    });
    document.addEventListener('DOMContentLoaded', function() {
        setTimeout(syncChuteTerminalHeight, 100);
        setTimeout(syncChuteTerminalHeight, 350);
    });
    var pillTab = document.getElementById('pill-sequence-tab');
    if (pillTab) {
        pillTab.addEventListener('click', function() {
            var globalFooter = document.getElementById('esp-global-card-footer');
            if (globalFooter) {
                globalFooter.classList.remove('d-flex');
                globalFooter.classList.add('d-none');
            }
            setTimeout(syncChuteTerminalHeight, 50);
            setTimeout(syncChuteTerminalHeight, 250);
        });
    }
}
