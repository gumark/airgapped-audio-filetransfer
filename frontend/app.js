const mode = new URLSearchParams(location.search).get('mode') || 'transmitter';
const $ = (id) => document.getElementById(id);
const state = { file: null, startedAt: 0, lastProgress: 0 };

function formatBytes(bytes) { if (!Number.isFinite(bytes)) return '—'; const units = ['B','KB','MB','GB','TB']; let i=0; while(bytes >= 1024 && i < units.length-1){bytes/=1024;i++} return `${bytes.toFixed(i ? 2 : 0)} ${units[i]}`; }
function setMode() {
  const receiver = mode === 'receiver';
  $('mode-label').textContent = receiver ? 'RECEIVER' : 'TRANSMITTER';
  $('page-title').textContent = receiver ? 'Receive through sound' : 'Send through sound';
  $('file-heading').textContent = receiver ? 'Incoming payload' : 'Choose a file';
  $('file-panel').classList.toggle('hidden', false);
  $('dropzone').classList.toggle('hidden', receiver);
  $('selected-file').classList.toggle('hidden', receiver || !state.file);
  $('security-panel').classList.toggle('hidden', false);
  $('start').textContent = receiver ? 'Start listening' : 'Start transmission';
  $('action-note').textContent = receiver ? 'The microphone is the only data input. Keep both computers physically isolated.' : 'The browser controls this computer only. Files leave through the selected audio device.';
  if (receiver) { $('file-detail').classList.add('hidden'); $('password-wrap').classList.add('visible'); }
}
async function api(url, options={}) { const response = await fetch(url, {headers:{'Content-Type':'application/json', ...(options.headers||{})}, ...options}); const data = await response.json(); if(!response.ok) throw new Error(data.detail || 'Request failed'); return data; }
function log(message) { const row = document.createElement('div'); row.textContent = message; $('log').prepend(row); }
function update(data) {
  const status = data.status || 'READY'; $('status-chip').textContent = status; $('activity-eyebrow').textContent = status === 'TRANSMITTING' || status === 'LISTENING' ? 'TRANSFER IN PROGRESS' : status;
  $('activity-title').textContent = status === 'COMPLETE' ? 'Transfer complete' : mode === 'receiver' ? (status === 'LISTENING' ? 'Listening for a signal' : 'Ready to receive') : (data.file ? data.file.name : 'Waiting for a payload');
  const progress = Number(data.progress || 0); $('progress-bar').style.width = `${Math.min(100, progress)}%`; $('progress-percent').textContent = `${progress.toFixed(1)}%`;
  $('progress-detail').textContent = data.metadata ? `${formatBytes((data.metadata.filesize || 0) * progress / 100)} / ${formatBytes(data.metadata.filesize)}` : (data.message || 'No active transfer');
  $('frames').textContent = data.frames_total ? `${data.frames_done} / ${data.frames_total}` : '—'; $('recovered').textContent = data.frames_recovered || 0; $('corrupted').textContent = data.frames_corrupted || 0;
  $('transfer-id').textContent = data.transfer_id ? `ID ${data.transfer_id}` : 'ID —'; $('stop').classList.toggle('hidden', !['TRANSMITTING','LISTENING'].includes(status)); $('start').classList.toggle('hidden', ['TRANSMITTING','LISTENING'].includes(status));
  if (data.metadata) { $('file-name').textContent = data.metadata.filename; $('file-size').textContent = formatBytes(data.metadata.filesize); $('file-hash').textContent = data.metadata.file_hash || 'verified at completion'; $('selected-file').classList.remove('hidden'); $('dropzone').classList.add('hidden'); }
  if (data.log && data.log.length) $('log').innerHTML = data.log.slice().reverse().map(item => `<div>${item.replaceAll('&','&amp;').replaceAll('<','&lt;')}</div>`).join('');
  if (progress > state.lastProgress && state.startedAt) { const elapsed = (Date.now()-state.startedAt)/1000; $('speed').textContent = elapsed ? `${formatBytes((data.metadata?.filesize || 0) * progress / 100 / elapsed)}/s` : '—'; state.lastProgress = progress; }
  if(status === 'COMPLETE') { $('link-status').textContent = 'Verified successfully'; $('signal-value').textContent = '✓'; $('tone-label').textContent = 'Hash verified'; }
}
async function upload(file) {
  state.file = file; $('file-name').textContent = file.name; $('file-size').textContent = formatBytes(file.size); $('selected-file').classList.remove('hidden'); $('dropzone').classList.add('hidden'); $('file-detail').classList.remove('hidden');
  $('file-hash').textContent = 'computed by the streaming backend before transmission';
  const form = new FormData(); form.append('file', file); const response = await fetch('/api/upload', {method:'POST', body:form}); if(!response.ok) throw new Error('Could not store file locally'); update(await response.json());
}
$('choose-file').onclick = () => $('file-input').click(); $('file-input').onchange = e => e.target.files[0] && upload(e.target.files[0]).catch(e => alert(e.message));
$('dropzone').ondragover = e => {e.preventDefault(); $('dropzone').style.borderColor='var(--blue)'}; $('dropzone').ondragleave = () => $('dropzone').style.borderColor=''; $('dropzone').ondrop = e => {e.preventDefault(); $('dropzone').style.borderColor=''; const file=e.dataTransfer.files[0]; if(file) upload(file).catch(err=>alert(err.message))};
$('clear-file').onclick = () => { state.file=null; $('selected-file').classList.add('hidden'); $('file-detail').classList.add('hidden'); $('dropzone').classList.remove('hidden'); };
$('encryption').onchange = e => $('password-wrap').classList.toggle('visible', e.target.checked || mode === 'receiver');
$('profile').onchange = e => { $('symbol-rate').textContent = ({MAXIMUM_RELIABILITY:'180 baud',BALANCED:'300 baud',MAXIMUM_SPEED:'500 baud'})[e.target.value]; };
$('calibrate').onclick = async () => { $('link-status').textContent='Analyzing local audio…'; try { const report=await api('/api/calibrate',{method:'POST',body:JSON.stringify({device:$('device').value})}); $('signal-value').textContent=report.signal_detected?'✓':'—'; $('link-status').textContent=report.reliability==='NONE'?'Ready for calibration':'Reliability: '+report.reliability; $('confidence').textContent=`${Math.round(report.frequency_confidence*100)}%`; $('snr').textContent=`${report.snr_db} dB`; $('link-copy').textContent=report.signal_detected?'Signal looks usable. Keep speakers and microphones aligned.':'Start the link test with both devices positioned nearby.'; } catch(e) { $('link-status').textContent=e.message; } };
$('start').onclick = async () => { try { state.startedAt=Date.now(); state.lastProgress=0; const options={fec_overhead:Number($('fec').value),compression:$('compression').value,encryption:$('encryption').checked,password:$('password').value,profile:$('profile').value,device:$('device').value}; update(await api('/api/start',{method:'POST',body:JSON.stringify(options)})); } catch(e) { alert(e.message); } };
$('stop').onclick = () => api('/api/stop',{method:'POST',body:'{}'}).then(update).catch(e=>alert(e.message));
$('export-log').onclick = async () => { const data=await api('/api/log'); const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='airgap-transfer-log.json'; a.click(); URL.revokeObjectURL(a.href); };
function draw() { const canvas=$('waveform'), rect=canvas.getBoundingClientRect(), dpr=window.devicePixelRatio||1; canvas.width=rect.width*dpr; canvas.height=rect.height*dpr; const ctx=canvas.getContext('2d'); ctx.scale(dpr,dpr); ctx.clearRect(0,0,rect.width,rect.height); ctx.strokeStyle='#dce3ff';ctx.lineWidth=1;ctx.beginPath(); for(let x=0;x<rect.width;x+=18){ctx.moveTo(x,0);ctx.lineTo(x,rect.height)}ctx.stroke(); ctx.strokeStyle='#7890ff';ctx.lineWidth=1.5;ctx.beginPath(); for(let x=0;x<rect.width;x++){const y=rect.height/2+Math.sin(x*.065+Date.now()*.002)*(5+Math.sin(x*.021)*3); x?ctx.lineTo(x,y):ctx.moveTo(x,y)}ctx.stroke(); requestAnimationFrame(draw); }
setMode(); draw(); fetch('/api/devices').then(r=>r.json()).then(devices=>{const select=$('device');select.innerHTML='';devices.filter(d=>mode==='receiver'?d.max_input_channels:d.max_output_channels).forEach(d=>{const option=document.createElement('option');option.value=d.index;option.textContent=`${d.name} · ${d.default_sample_rate} Hz`;select.append(option)});if(!select.options.length)select.innerHTML='<option>No PortAudio devices found</option>'}).catch(()=>{});
const ws = new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws`); ws.onmessage=e=>update(JSON.parse(e.data)); ws.onclose=()=>setTimeout(()=>location.reload(),3000);
