const mode = new URLSearchParams(location.search).get('mode') || 'transmitter';
const $ = (id) => document.getElementById(id);
const state = { startedAt: 0, lastProgress: 0 };
const profiles = {
  MAXIMUM_RELIABILITY: { label: '180 baud', fec: '40% FEC', symbol_rate: 180, fec_overhead: 40 },
  BALANCED: { label: '300 baud', fec: '25% FEC', symbol_rate: 300, fec_overhead: 25 },
  MAXIMUM_SPEED: { label: '500 baud', fec: '10% FEC', symbol_rate: 500, fec_overhead: 10 },
};

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let unit = 0;
  while (bytes >= 1024 && unit < units.length - 1) { bytes /= 1024; unit += 1; }
  return `${bytes.toFixed(unit ? 2 : 0)} ${units[unit]}`;
}

function escapeHtml(value) {
  return String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}

async function api(url, options = {}) {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Request failed');
  return data;
}

function configureMode() {
  const receiver = mode === 'receiver';
  $('mode-label').textContent = receiver ? 'RECEIVER' : 'TRANSMITTER';
  $('page-title').textContent = receiver ? 'Receive a file through sound.' : 'Send a file through sound.';
  $('page-copy').textContent = receiver ? 'Listen for an encrypted transfer from a nearby computer. Sound is the data channel.' : 'No network connection is used during transfer. Sound is the data channel.';
  $('file-heading').textContent = receiver ? 'Incoming file' : 'Choose a file';
  $('file-copy').textContent = receiver ? 'File details appear when a signal is detected.' : 'Files are read locally and streamed in small chunks.';
  $('dropzone').classList.toggle('hidden', receiver);
  $('receiver-empty').classList.toggle('hidden', !receiver);
  $('clear-file').classList.toggle('hidden', receiver);
  $('start').textContent = receiver ? 'Start listening' : 'Start transmission';
  $('password-wrap').classList.toggle('hidden', !receiver && !$('encryption').checked);
}

function update(data) {
  const status = data.status || 'READY';
  const active = ['TRANSMITTING', 'LISTENING'].includes(status);
  const progress = Number(data.progress || 0);
  $('status-chip').textContent = status;
  $('activity-eyebrow').textContent = active ? 'IN PROGRESS' : status;
  $('activity-title').textContent = status === 'COMPLETE' ? 'Transfer complete' : mode === 'receiver' ? (active ? 'Listening for a signal' : 'Ready to receive') : (data.file?.name || 'Ready to transfer');
  $('progress-bar').style.width = `${Math.min(100, progress)}%`;
  $('progress-percent').textContent = `${progress.toFixed(1)}%`;
  $('progress-detail').textContent = data.metadata ? `${formatBytes((data.metadata.filesize || 0) * progress / 100)} / ${formatBytes(data.metadata.filesize)}` : (data.message || 'Select a file to begin');
  $('frames').textContent = data.frames_total ? `${data.frames_done} / ${data.frames_total}` : '—';
  $('recovered').textContent = data.frames_recovered || 0;
  $('corrupted').textContent = data.frames_corrupted || 0;
  $('transfer-id').textContent = data.transfer_id ? `ID ${data.transfer_id}` : '—';
  $('stop').classList.toggle('hidden', !active);
  $('start').classList.toggle('hidden', active);

  if (data.metadata) {
    $('file-name').textContent = data.metadata.filename;
    $('file-size').textContent = formatBytes(data.metadata.filesize);
    $('file-hash').textContent = data.metadata.file_hash || 'Verified at completion';
    $('selected-file').classList.remove('hidden');
    $('receiver-empty').classList.add('hidden');
    $('file-detail').classList.remove('hidden');
  }
  if (data.log?.length) $('log').innerHTML = data.log.slice().reverse().map((item) => `<div>${escapeHtml(item)}</div>`).join('');
  if (progress > state.lastProgress && state.startedAt) state.lastProgress = progress;
  if (status === 'COMPLETE') $('progress-detail').textContent = 'Hash verified';
}

async function upload(file) {
  $('file-name').textContent = file.name;
  $('file-size').textContent = formatBytes(file.size);
  $('selected-file').classList.remove('hidden');
  $('dropzone').classList.add('hidden');
  $('file-detail').classList.remove('hidden');
  $('file-hash').textContent = 'Calculated by the backend before transmission';
  const form = new FormData();
  form.append('file', file);
  const response = await fetch('/api/upload', { method: 'POST', body: form });
  if (!response.ok) throw new Error('Could not store the file locally');
  update(await response.json());
}

$('choose-file').onclick = () => $('file-input').click();
$('file-input').onchange = (event) => event.target.files[0] && upload(event.target.files[0]).catch((error) => alert(error.message));
$('dropzone').ondragover = (event) => { event.preventDefault(); $('dropzone').classList.add('dragging'); };
$('dropzone').ondragleave = () => $('dropzone').classList.remove('dragging');
$('dropzone').ondrop = (event) => { event.preventDefault(); $('dropzone').classList.remove('dragging'); const file = event.dataTransfer.files[0]; if (file) upload(file).catch((error) => alert(error.message)); };
$('clear-file').onclick = async () => { try { update(await api('/api/clear-file', { method: 'POST', body: '{}' })); $('selected-file').classList.add('hidden'); $('file-detail').classList.add('hidden'); $('dropzone').classList.remove('hidden'); } catch (error) { alert(error.message); } };
$('encryption').onchange = (event) => { if (mode !== 'receiver') $('password-wrap').classList.toggle('hidden', !event.target.checked); };
$('profile').onchange = (event) => { const profile = profiles[event.target.value]; $('symbol-rate').textContent = profile.label; $('fec-value').textContent = profile.fec; };

$('calibrate').onclick = async () => {
  $('link-status').textContent = 'Testing local audio…';
  $('link-metrics').classList.add('hidden');
  try {
    const report = await api('/api/calibrate', { method: 'POST', body: JSON.stringify({ device: $('device').value }) });
    $('link-status').textContent = report.signal_detected ? `Signal detected · ${report.reliability.toLowerCase()} reliability` : 'No signal detected';
    $('snr').textContent = `${report.snr_db} dB`;
    $('confidence').textContent = `${Math.round(report.frequency_confidence * 100)}%`;
    $('link-metrics').classList.remove('hidden');
  } catch (error) {
    $('link-status').textContent = error.message;
  }
};

$('start').onclick = async () => {
  const profile = profiles[$('profile').value];
  const options = { ...profile, compression: $('compression').value, encryption: $('encryption').checked, password: $('password').value, device: $('device').value };
  try {
    state.startedAt = Date.now();
    state.lastProgress = 0;
    update(await api('/api/start', { method: 'POST', body: JSON.stringify(options) }));
  } catch (error) { alert(error.message); }
};
$('stop').onclick = () => api('/api/stop', { method: 'POST', body: '{}' }).then(update).catch((error) => alert(error.message));
$('export-log').onclick = async () => {
  const data = await api('/api/log');
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  link.download = 'airgap-transfer-log.json';
  link.click();
  URL.revokeObjectURL(link.href);
};

configureMode();
$('profile').dispatchEvent(new Event('change'));
fetch('/api/devices').then((response) => response.json()).then((devices) => {
  const select = $('device');
  select.innerHTML = '';
  devices.filter((device) => mode === 'receiver' ? device.max_input_channels : device.max_output_channels).forEach((device) => {
    const option = document.createElement('option');
    option.value = device.index;
    option.textContent = `${device.name} · ${device.default_sample_rate} Hz`;
    select.append(option);
  });
  if (!select.options.length) select.innerHTML = '<option value="">No audio devices found</option>';
}).catch(() => {});

const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
socket.onmessage = (event) => update(JSON.parse(event.data));
socket.onclose = () => setTimeout(() => location.reload(), 3000);
