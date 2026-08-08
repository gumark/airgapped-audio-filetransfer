/**
 * Shared JavaScript utilities for the Air-Gapped Audio Transfer UI.
 * Works standalone in the browser without a Python backend.
 */

// --- Utility Functions ---

function formatSize(bytes) {
    if (bytes === 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(2) + ' ' + units[i];
}

function formatSpeed(bytesPerSecond) {
    if (bytesPerSecond === 0) return '0 B/s';
    return formatSize(bytesPerSecond) + '/s';
}

function formatTime(seconds) {
    if (!seconds || !isFinite(seconds)) return '--';
    if (seconds < 60) return Math.round(seconds) + 's';
    const minutes = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return minutes + 'm ' + secs + 's';
}

function formatHash(hash, length = 16) {
    if (!hash) return '—';
    return hash.substring(0, length) + '...';
}

// --- Logging ---

function addLog(message, level = 'INFO') {
    const container = document.getElementById('logContainer');
    if (!container) return;

    const entry = document.createElement('div');
    entry.className = 'log-entry';

    const timestamp = new Date().toLocaleTimeString('en-US', { hour12: false });

    entry.innerHTML = `
        <span class="log-timestamp">${timestamp}</span>
        <span class="log-message" style="color: ${level === 'ERROR' ? 'var(--danger)' : 'var(--text-secondary)'}">${message}</span>
    `;

    container.appendChild(entry);
    container.scrollTop = container.scrollHeight;

    // Keep only last 100 entries
    while (container.children.length > 100) {
        container.removeChild(container.firstChild);
    }
}

function exportLog() {
    const container = document.getElementById('logContainer');
    if (!container) return;

    const entries = [];
    container.querySelectorAll('.log-entry').forEach(entry => {
        entries.push({
            timestamp: entry.querySelector('.log-timestamp').textContent,
            message: entry.querySelector('.log-message').textContent,
        });
    });

    const blob = new Blob([JSON.stringify(entries, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'transfer_log.json';
    a.click();
    URL.revokeObjectURL(url);
}

// --- Event Listeners ---

document.addEventListener('DOMContentLoaded', () => {
    // Display mode from URL or localStorage
    const mode = localStorage.getItem('transferMode');
    if (mode) {
        console.log('Transfer mode:', mode);
    }

    // Update sample rate display
    const sampleRateEl = document.getElementById('sampleRate');
    if (sampleRateEl) {
        // Try to get actual sample rate from Web Audio API
        try {
            const testContext = new (window.AudioContext || window.webkitAudioContext)();
            sampleRateEl.textContent = testContext.sampleRate.toLocaleString() + ' Hz';
            testContext.close();
        } catch (e) {
            sampleRateEl.textContent = '48,000 Hz';
        }
    }
});
