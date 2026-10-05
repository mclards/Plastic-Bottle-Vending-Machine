// --- CHUTE SEQUENCE & SYSTEM LOGGER CLIENT ENGINE ---
var sequencePollTimer = null;
var systemLogPollTimer = null;
var lastKnownLogId = 0;
var cachedSystemLogs = [];
var cachedChuteEvents = [];
var seqTerminalDark = true;

try {
    var storedTheme = localStorage.getItem('ecofi_seq_term_theme');
    if (storedTheme !== null) {
        seqTerminalDark = (storedTheme === 'dark');
    }
} catch(e) {}

function toggleSequenceTerminalTheme() {
    seqTerminalDark = !seqTerminalDark;
    try {
        localStorage.setItem('ecofi_seq_term_theme', seqTerminalDark ? 'dark' : 'light');
    } catch(e) {}
    applySequenceTerminalTheme();
    if (cachedChuteEvents && cachedChuteEvents.length > 0) {
        renderChuteLogTerminal(cachedChuteEvents);
    }
}

function applySequenceTerminalTheme() {
    var termBody = document.getElementById('seq-term-body');
    var term = document.getElementById('seq-log-terminal');
    var btn = document.getElementById('btn-seq-term-theme');
    if (btn) {
        btn.innerHTML = '<i class="fas fa-adjust mr-1"></i>' + (seqTerminalDark ? 'Dark' : 'Light');
    }
    if (!termBody || !term) return;
    if (seqTerminalDark) {
        termBody.style.background = '#0b0f19';
        termBody.style.border = '1px solid rgba(255,255,255,0.1)';
        termBody.style.borderTop = 'none';
        term.style.color = '#f8fafc';
    } else {
        termBody.style.background = '#f8fafc';
        termBody.style.border = '1px solid var(--eco-border)';
        termBody.style.borderTop = 'none';
        term.style.color = '#0f172a';
    }
}

function startSequencePolling() {
    if (sequencePollTimer) return;
    applySequenceTerminalTheme();
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
        statBadge.className = 'badge px-2 py-0 font-weight-bold ml-2 ' + (
            status === 'PASSED' ? 'badge-success' :
            status === 'SCANNING' ? 'badge-warning' :
            status === 'REJECTED' ? 'badge-danger' :
            status === 'TIMEOUT' ? 'badge-warning' : 'badge-secondary'
        );
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
            stateEl.className = 'badge px-2 py-0 ' + (isPass ? 'badge-success' : (isAct ? 'badge-warning' : (isFail ? 'badge-danger' : 'badge-secondary')));
        }

        if (rowStat) {
            rowStat.innerHTML = '<span class="badge px-1 ' + (isPass ? 'badge-success' : (isAct ? 'badge-warning' : (isFail ? 'badge-danger' : 'badge-secondary'))) + '">' + (isPass ? 'OK' : (isAct ? 'RUN' : (isFail ? 'FAIL' : 'WAIT'))) + '</span>';
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
        badgeTot.textContent = status;
        badgeTot.className = 'badge px-1 ' + (status === 'PASSED' ? 'badge-success' : (status === 'REJECTED' ? 'badge-danger' : 'badge-secondary'));
    }

    var evList = data.events || data.recent_logs;
    if (evList && evList.length > 0) {
        renderChuteLogTerminal(evList);
    }
}

function renderChuteLogTerminal(events) {
    var term = document.getElementById('seq-log-terminal');
    if (!term) return;
    cachedChuteEvents = events;
    applySequenceTerminalTheme();

    var isDark = seqTerminalDark;
    var lines = events.map(function(ev) {
        var ts = ev.timestamp || ev.ts || '';
        var stage = ev.stage || ev.category || 'SYS';
        var msg = ev.message || ev.msg || '';

        // High-contrast, crystal-clear timestamp
        var tsColor = isDark ? '#94a3b8' : '#475569';
        var tsHtml = '<span style="color:' + tsColor + ';font-weight:600;">[' + escapeHtml(ts) + ']</span>';

        // Stage badges: bright bold colored chips
        var badgeBg = '#334155';
        var badgeText = '#ffffff';
        if (stage === 'CHUTE') { badgeBg = '#0284c7'; }
        else if (stage === 'SENSOR') { badgeBg = '#7c3aed'; }
        else if (stage === 'ACTUATOR') { badgeBg = '#d97706'; }
        else if (stage === 'SYSTEM') { badgeBg = isDark ? '#475569' : '#64748b'; }

        var stageHtml = '<span style="background:' + badgeBg + ';color:' + badgeText + ';font-weight:700;font-size:9.5px;padding:1px 5px;border-radius:3px;margin-right:4px;">' + escapeHtml(stage) + '</span>';

        // Message text with high-contrast syntax highlighting
        var msgColor = isDark ? '#f8fafc' : '#0f172a';
        if (msg.indexOf('REJECT') !== -1 || msg.indexOf('FAIL') !== -1 || msg.indexOf('TIMEOUT') !== -1) {
            msgColor = isDark ? '#fca5a5' : '#dc2626';
        } else if (msg.indexOf('MEASURE') !== -1 || msg.indexOf('SCAN') !== -1 || msg.indexOf('Cal-W=') !== -1 || msg.indexOf('Mass:') !== -1) {
            msgColor = isDark ? '#fde047' : '#b45309';
        } else if (msg.indexOf('CONFIRMED') !== -1 || msg.indexOf('ACCEPTED') !== -1 || msg.indexOf('saved') !== -1 || msg.indexOf('Authentic') !== -1 || msg.indexOf('OK') !== -1) {
            msgColor = isDark ? '#4ade80' : '#15803d';
        } else if (msg.indexOf('OPENED') !== -1 || msg.indexOf('TRIGGERED') !== -1) {
            msgColor = isDark ? '#60a5fa' : '#1d4ed8';
        }

        return tsHtml + ' ' + stageHtml + ' <span style="color:' + msgColor + ';font-weight:500;">' + escapeHtml(msg) + '</span>';
    });

    term.innerHTML = lines.join('\n');
    term.scrollTop = term.scrollHeight;
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
        var msgColor = '#f8fafc';
        if (lvl === 'TRIGGER') { lvlBadge = 'badge-primary'; msgColor = '#93c5fd'; }
        else if (lvl === 'MEASURE') { lvlBadge = 'badge-warning'; msgColor = '#fde68a'; }
        else if (lvl === 'SUCCESS') { lvlBadge = 'badge-success'; msgColor = '#86efac'; }
        else if (lvl === 'WARN') { lvlBadge = 'badge-warning'; msgColor = '#fdba74'; }
        else if (lvl === 'ERROR') { lvlBadge = 'badge-danger'; msgColor = '#fca5a5'; }

        return '<span style="color:#94a3b8;font-weight:600;">[' + ts + ']</span> ' +
               '<span class="badge ' + lvlBadge + ' px-1 font-mono" style="font-size:9.5px;">' + lvl + '</span> ' +
               '<span class="badge badge-dark border border-secondary px-1 text-info font-mono" style="font-size:9.5px;">' + cat + '</span> ' +
               '<span style="color:' + msgColor + ';font-weight:500;">' + escapeHtml(msg) + '</span>';
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
                    cachedChuteEvents = [];
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
