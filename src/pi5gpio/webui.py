"""Single-page browser UI served by `pi5-gpio --serve` (no external assets, works offline)."""

INDEX_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pi 5 GPIO Controller</title>
<style>
 body{font-family:system-ui,sans-serif;margin:0;background:#f4f6f8;color:#222}
 header{display:flex;align-items:center;gap:12px;padding:8px 14px;background:#263238;color:#fff;flex-wrap:wrap}
 header h1{font-size:18px;margin:0;flex:1}
 #sim{background:#ffd54f;color:#000;padding:2px 8px;border-radius:4px;display:none}
 #warn{background:#fff3cd;padding:6px 14px;font-size:13px}
 #alloff{background:#c62828;color:#fff;border:0;padding:8px 16px;font-weight:bold;border-radius:4px;cursor:pointer}
 main{display:flex;gap:16px;padding:14px;flex-wrap:wrap}
 #hdr{background:#fff;border:1px solid #ccc;border-radius:6px;padding:10px 14px}
 .row{display:flex;align-items:center;height:26px}
 .lab{width:105px;font-size:12px}.lab.l{text-align:right}
 .pin{width:20px;height:20px;border-radius:50%;border:2px solid #000;margin:0 6px;box-sizing:border-box;cursor:pointer}
 .pin.fixed{cursor:default}
 #panel{background:#fff;border:1px solid #ccc;border-radius:6px;padding:14px;min-width:340px;max-width:430px;flex:1}
 fieldset{margin:10px 0;border:1px solid #bbb;border-radius:4px}
 #tog{width:170px;height:56px;font-size:20px;font-weight:bold;border:0;border-radius:6px;color:#fff;background:#9e9e9e;cursor:pointer}
 #tog:disabled{opacity:.6;cursor:default}
 input[type=number]{width:90px} small{color:#555} #msg{padding:6px 14px;font-size:13px;min-height:18px}
 .err{color:#b71c1c}
</style></head><body>
<header><h1>Raspberry Pi 5 GPIO</h1><span id="sim">SIMULATION - no real pins</span>
 <button id="alloff">ALL OFF</button></header>
<div id="warn">GPIO is <b>3.3 V only</b> - never connect 5 V to a pin. About 16 mA max per pin: use a resistor for LEDs and a transistor/relay driver for anything bigger.</div>
<div id="msg"></div>
<main>
 <div id="hdr"></div>
 <div id="panel">
  <h2 id="ptitle" style="margin-top:0">Click a GPIO pin</h2><div id="pinfo"></div>
  <fieldset><legend>Mode</legend>
   <label><input type="radio" name="mode" value="unused"> Unused</label>
   <label><input type="radio" name="mode" value="input"> Input</label>
   <label><input type="radio" name="mode" value="output"> Output</label></fieldset>
  <button id="tog" disabled>OFF</button> <div id="lvl"></div>
  <fieldset><legend>Software timer (auto toggle)</legend>
   Period (ms) <input id="per" type="number" value="10" min="1" step="any">
   Duty ON (%) <input id="duty" type="number" value="50" min="1" max="99">
   <button id="tstart">Start</button> <button id="tstop">Stop</button><br>
   <small>One full ON+OFF cycle = period (10 ms @ 50% = 5 ms ON, 5 ms OFF). Software timing has jitter; minimum 1 ms.</small>
   <div id="stats"></div></fieldset>
  <fieldset><legend>Hardware PWM (GPIO 12/13/18/19)</legend>
   Freq (Hz) <input id="freq" type="number" value="100" min="1" step="any">
   Duty ON (%) <input id="hduty" type="number" value="50" min="1" max="99">
   <button id="hstart">Start</button> <button id="hstop">Stop</button><br><small id="hnote"></small></fieldset>
 </div>
</main>
<script>
let token = sessionStorage.getItem('tok') || new URLSearchParams(location.hash.slice(1)).get('token') || '';
let layout = [], snap = null, sel = null, busy = false;
const $ = id => document.getElementById(id);
const COL = {gnd:'#303030','3v3':'#f0a030','5v':'#d03030',id:'#8060b0'};
function msg(t, err){ $('msg').textContent = t || ''; $('msg').className = err ? 'err' : ''; }
async function api(path, body){
  const o = {headers:{Authorization:'Bearer ' + token}};
  if (body){ o.method='POST'; o.body=JSON.stringify(body); o.headers['Content-Type']='application/json'; }
  const r = await fetch(path, o);
  if (r.status === 401){ token = prompt('Enter the access token printed by pi5-gpio --serve:') || ''; sessionStorage.setItem('tok', token); throw new Error('Not authorized - retry'); }
  return r.json();
}
async function call(method, args, kwargs, retry){
  const r = await api('/api/call', {method, args, kwargs: kwargs || {}});
  if (r.error){
    if (r.type === 'SpecialPinError' && !retry &&
        confirm('GPIO' + r.bcm + ' is used for ' + r.function + '.\nDriving it may break connected hardware.\n\nContinue anyway?'))
      return call(method, args, Object.assign({}, kwargs, {allow_special:true}), true);
    if (r.type !== 'SpecialPinError') msg(r.error, true);
    return null;
  }
  msg(''); await refresh(); return r;
}
function build(){
  const h = $('hdr'); h.innerHTML = '';
  for (let i = 0; i < 20; i++){
    const row = document.createElement('div'); row.className = 'row';
    [layout[2*i], layout[2*i+1]].forEach((p, k) => {
      const lab = document.createElement('span'); lab.className = 'lab' + (k ? '' : ' l');
      lab.textContent = k ? '(' + p.physical + ') ' + p.label + (p.special ? '*' : '') : p.label + (p.special ? '*' : '') + ' (' + p.physical + ')';
      const d = document.createElement('div'); d.className = 'pin' + (p.bcm === null ? ' fixed' : ''); d.id = 'p' + p.physical;
      d.style.background = COL[p.kind] || '#ccd6e0';
      d.onclick = () => click(p);
      if (k) row.append(d, lab); else row.append(lab, d);
    });
    h.append(row);
  }
  const n = document.createElement('small'); n.textContent = '* special function pin'; h.append(n);
}
function click(p){
  if (p.bcm === null){ msg('Pin ' + p.physical + ' (' + p.label + ') cannot be controlled.'); return; }
  sel = p; paint();
}
function paint(){
  if (!snap) return;
  $('sim').style.display = snap.simulated ? 'inline' : 'none';
  layout.forEach(p => {
    if (p.bcm === null) return;
    const s = snap.pins[p.bcm], d = $('p' + p.physical);
    d.style.background = s.mode === 'unused' ? '#ccd6e0' : s.timer ? '#42a5f5' :
      s.mode === 'output' ? (s.value ? '#43a047' : '#1b5e20') : (s.value ? '#ffee58' : '#9e9d24');
    d.style.outline = sel && sel.bcm === p.bcm ? '3px solid #e91e63' : 'none';
  });
  const t = $('tog');
  if (!sel){ t.disabled = true; return; }
  const s = snap.pins[sel.bcm];
  $('ptitle').textContent = 'GPIO' + sel.bcm + ' (physical pin ' + sel.physical + ')';
  $('pinfo').textContent = sel.special ? 'Special function: ' + sel.special : 'General purpose pin';
  document.querySelectorAll('input[name=mode]').forEach(r => r.checked = r.value === s.mode);
  t.disabled = !!s.timer;
  t.textContent = s.timer ? 'TIMER RUNNING' : s.mode === 'output' ? (s.value ? 'ON' : 'OFF') : 'Click to enable';
  t.style.background = s.timer ? '#42a5f5' : s.mode === 'output' && s.value ? '#43a047' : '#9e9e9e';
  $('lvl').textContent = s.mode === 'input' ? 'Input level: ' + (s.value ? 'HIGH' : 'LOW') : '';
  const st = s.stats;
  $('stats').textContent = s.timer === 'software' && st ? 'Toggles ' + st.toggles + ', late avg ' +
    (st.toggles ? Math.round(st.total_late_us / st.toggles) : 0) + ' us, max ' + Math.round(st.max_late_us) + ' us' + (st.error ? ' ERROR ' + st.error : '') :
    s.timer === 'hardware' ? 'Hardware PWM running at ' + (1 / s.period_s) + ' Hz' : '';
  const hw = [12,13,18,19].includes(sel.bcm);
  $('hstart').disabled = !(hw && snap.pwm_available);
  $('hnote').textContent = !hw ? 'This pin has no hardware PWM.' : !snap.pwm_available ? 'Not available: enable the pwm overlay in config.txt and reboot (see README).' : '';
}
async function refresh(){
  if (busy) return; busy = true;
  try { snap = await api('/api/snapshot'); paint(); } catch (e) { msg(e.message, true); } finally { busy = false; }
}
document.querySelectorAll('input[name=mode]').forEach(r => r.onchange = () => {
  if (!sel) return;
  call(r.value === 'output' ? 'set_output' : r.value === 'input' ? 'set_input' : 'release', [sel.bcm]);
});
$('tog').onclick = async () => {
  if (!sel) return;
  if (snap.pins[sel.bcm].mode !== 'output'){ if (!(await call('set_output', [sel.bcm]))) return; await call('set_value', [sel.bcm, true]); return; }
  call('set_value', [sel.bcm, !snap.pins[sel.bcm].value]);
};
$('tstart').onclick = () => sel && call('start_timer', [sel.bcm, $('per').value / 1000, $('duty').value / 100]);
$('hstart').onclick = () => sel && call('start_hw_pwm', [sel.bcm, +$('freq').value, $('hduty').value / 100]);
$('tstop').onclick = $('hstop').onclick = () => sel && call('stop_timer', [sel.bcm]);
$('alloff').onclick = () => call('all_off', []);
(async () => {
  layout = await (await fetch('/api/layout')).json(); build();
  if (!token){ token = prompt('Enter the access token printed by pi5-gpio --serve:') || ''; sessionStorage.setItem('tok', token); }
  refresh(); setInterval(refresh, 300);
})();
</script></body></html>
"""
