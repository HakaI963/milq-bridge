/* MilqBridge UI — talks to the bridge API. No frameworks, no CDN, works offline. */
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const API = {
  get: (p, auth=false) => fetch(p, {headers: auth?authH():{}}).then(r=>r.json()),
  post: (p, b) => fetch(p, {method:'POST', headers:{'Content-Type':'application/json', ...authH()}, body: JSON.stringify(b||{})}).then(async r => ({status:r.status, json: await r.json().catch(()=>({}))}))
};
let KEY = localStorage.getItem('milq_key') || '';
function authH(){ return KEY ? {'Authorization':'Bearer '+KEY} : {}; }
async function gA(p){ const r = await fetch(p,{headers:authH()}); if(r.status===401) throw new Error('401: set API key'); return r.json(); }

function toast(msg, ok=true){ const t=$('#toast'); t.textContent=msg; t.style.borderColor= ok?'var(--acc)':'var(--bad)'; t.hidden=false; clearTimeout(t._h); t._h=setTimeout(()=>t.hidden=true, 2600); }
function esc(s){ return String(s??'').replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
async function copyText(txt){ try{ await navigator.clipboard.writeText(txt); toast('Copied'); }catch(e){ const t=document.createElement('textarea'); t.value=txt; document.body.append(t); t.select(); document.execCommand('copy'); t.remove(); toast('Copied'); } }
const fmtT = ts => new Date(ts*1000).toLocaleTimeString();

/* ---------- nav ---------- */
$$('#nav button').forEach(b => b.onclick = () => {
  $$('#nav button').forEach(x=>x.classList.remove('on')); b.classList.add('on');
  $$('.view').forEach(v=>v.classList.remove('on'));
  $('#view-'+b.dataset.view).classList.add('on');
  refresh();
});
function go(v){ document.querySelector(`[data-view=${v}]`).click(); }
document.addEventListener('click', e => {
  const c = e.target.closest('[data-copy]');
  if(c){ copyText($('#'+c.dataset.copy).textContent.trim()); return; }
  const ct = e.target.closest('[data-copytext]');
  if(ct){ copyText($('#'+ct.dataset.copytext).textContent.trim()); return; }
  const g = e.target.closest('[data-go]');
  if(g){ go(g.dataset.go); }
});

/* ---------- key bootstrap: loopback/local endpoint serves it to the UI on the same host ---------- */
async function ensureKey(){
  const u = new URLSearchParams(location.search);
  if(u.get('key')){ KEY=u.get('key'); localStorage.setItem('milq_key',KEY); return; }
  if(KEY) return;
  try{
    const d = await fetch('/local/key').then(r=>r.json());
    if(d.key){ KEY=d.key; localStorage.setItem('milq_key',KEY); }
  }catch(e){}
}

/* ---------- login: codex device flow ---------- */
let pollTimer=null;
$('#cx-start').onclick = async () => {
  $('#cx-start').disabled=true;
  const res = await API.post('/login/codex/start');
  const s = res.json;
  $('#cx-idle').hidden=true; $('#cx-err').hidden=true;
  if(res.status!==200 || s.stage==='error'){ $('#cx-err').hidden=false; $('#cx-err').textContent='Start failed: '+(s.error||JSON.stringify(s)); $('#cx-start').disabled=false; return; }
  $('#cx-wait').hidden=false; $('#cx-done').hidden=true;
  $('#cx-code').textContent=s.user_code; $('#cx-url').href=s.verification_url;
  pollLogin();
};
$('#cx-copy').onclick=()=>copyText($('#cx-code').textContent);
$('#cx-again').onclick=()=>{ $('#cx-done').hidden=true; $('#cx-idle').hidden=false; $('#cx-start').disabled=false; };
async function pollLogin(){
  clearInterval(pollTimer);
  pollTimer = setInterval(async ()=>{
    try{
      const s = await API.get('/login/codex/status');
      if(s.stage==='ready' || s.signed_in){
        clearInterval(pollTimer);
        $('#cx-wait').hidden=true; $('#cx-done').hidden=false;
        $('#cx-plan').textContent = s.plan ? ('plan: '+s.plan) : 'connected';
        $('#cx-acc').textContent = s.account_id ? ('account '+s.account_id) : '';
        toast('ChatGPT connected'); refresh();
      } else if(s.stage==='error' || s.stage==='expired'){
        clearInterval(pollTimer);
        $('#cx-wait').hidden=true; $('#cx-err').hidden=false;
        $('#cx-err').textContent = s.error || 'login failed';
        $('#cx-start').disabled=false;
      } else {
        $('#cx-state').textContent = `Waiting for approval… (${s.polls||0} polls)`;
      }
    }catch(e){}
  }, 2000);
}

/* ---------- login: google ---------- */
// Google URL is loaded when Login view renders (loadGoogleUrl)
$('#gg-ex').onclick = async () => {
  let raw = $('#gg-code').value.trim();
  const m = raw.match(/[?&]code=([^&#]+)/); const code = m ? m[1] : raw;
  if(!code) return toast('paste the code first', false);
  const r = await API.post('/login/google/exchange', {code});
  $('#gg-out').hidden=false; $('#gg-out').textContent = JSON.stringify(r.json, null, 1);
};
// fill Google URL on view open
async function loadGoogleUrl(){
  const r = await API.post('/login/google/start',{});
  if(r.json.authorize_url) $('#gg-url').href = r.json.authorize_url;
}

/* ---------- models ---------- */
let MODELS=[], tierFilter='';
async function loadModels(){
  try{ MODELS = (await gA('/v1/models')).data || []; }
  catch(e){ MODELS=[]; renderModels(); return; }
  renderModels();
}
function renderModels(){
  const grid=$('#modelgrid'); const sel=$('#ov-model');
  const list = MODELS.filter(m=>!tierFilter || (m.milq.tier===tierFilter));
  grid.innerHTML = list.map(m=>{
    const t=m.milq.tier;
    const badge = t==='free' ? '<span class="badge green">FREE · daily sips</span>' : t==='plan' ? '<span class="badge blue">CHATGPT PLAN</span>' : '<span class="badge amber">PREMIUM</span>';
    return `<div class="card mcard"><h4>${esc(m.milq.display)}</h4><div class="mid">${esc(m.id)}</div>
      <div class="meta">${badge}<span class="badge blue" style="opacity:.75">${esc(m.milq.api_format)}</span><span class="badge" style="border-color:var(--line);color:var(--mut)">${(m.milq.context_window/1000)}k ctx</span></div>
      <div class="acts"><button class="mini" data-test="${esc(m.id)}">Test</button><button class="mini" data-mcopy="${esc(m.id)}">copy id</button>
      ${t==='premium'?`<button class="mini" data-unlock="${esc(m.id)}">watch ad → unlock 1h</button>`:''}</div></div>`;
  }).join('') || '<p class="muted">Models need a valid API key — paste it in Settings on the Agent setup page.</p>';
  if(sel) sel.innerHTML = list.map(m=>`<option>${esc(m.id)}</option>`).join('');
  grid.querySelectorAll('[data-test]').forEach(b=>b.onclick=()=>{ go('overview'); $('#ov-model').value=b.dataset.test; $('#ov-run').click(); });
  grid.querySelectorAll('[data-mcopy]').forEach(b=>b.onclick=()=>copyText(b.dataset.mcopy));
  grid.querySelectorAll('[data-unlock]').forEach(b=>b.onclick=async()=>{
    const r=await API.post('/ads/unlock',{model:b.dataset.unlock});
    toast(r.json.ok?('Unlocked '+b.dataset.unlock+' for 1h'):('locked: '+JSON.stringify(r.json)), r.json.ok);
  });
}
$$('#tierchips .chip').forEach(c=>c.onclick=()=>{ $$('#tierchips .chip').forEach(x=>x.classList.remove('on')); c.classList.add('on'); tierFilter=c.dataset.t; renderModels(); });

/* ---------- overview test ---------- */
$('#ov-run').onclick = async () => {
  const out=$('#ov-out'); out.hidden=false; out.textContent='…';
  const model=$('#ov-model').value||'gpt-5-codex';
  const r = await API.post('/v1/chat/completions', {model, messages:[{role:'user',content:$('#ov-prompt').value||'hi'}], stream:false, ...authH?{}:{}});
  const j = r.json;
  if(r.status===200 && j.choices) out.textContent = j.choices.map(c=>c.message.content).join('\n') || JSON.stringify(j,null,1);
  else if(r.status===402) out.textContent='PREMIUM LOCKED: '+ (j.error?.message||'') +'\nUse “watch ad → unlock” on the Models page.';
  else out.textContent='HTTP '+r.status+'\n'+JSON.stringify(j,null,1);
  refresh();
};

/* ---------- usage ---------- */
async function loadUsage(){
  let d; try{ d=await gA('/usage'); }catch(e){ return; }
  const u=d.local;
  $('#u-reqs').textContent=u.requests; $('#u-in').textContent=u.in_tokens; $('#u-out').textContent=u.out_tokens;
  $('#u-bymodel').innerHTML=Object.entries(u.by_model).map(([k,v])=>`<tr><td>${esc(k)}</td><td style="text-align:right">${v}×</td></tr>`).join('')||'<tr><td class="muted">no requests yet</td></tr>';
  $('#u-wham').textContent = d.chatgpt_wham && Object.keys(d.chatgpt_wham).length ? JSON.stringify(d.chatgpt_wham,null,1) : (CODEX_SIGNED? 'reported empty by OpenAI (plan meter may be unavailable)' : 'not signed in — ChatGPT meter unavailable');
  $('#u-log').innerHTML=(u.log||[]).slice(0,30).map(r=>`<tr><td>${fmtT(r.ts)}</td><td>${esc(r.model)}</td><td>${esc(r.tier)}</td><td><span class="badge ${r.status<400?'green':(r.status===402?'amber':'red')}">${r.status}</span></td><td>${r.in}/${r.out}</td><td>${r.ms}</td><td>${esc(r.err||'')}</td></tr>`).join('');
}
let CODEX_SIGNED=false;

/* ---------- overview refresh ---------- */
async function refresh(){
  try{
    const s = await API.get('/login/codex/status');
    CODEX_SIGNED = !!s.signed_in;
    const b = s.signed_in ? `<span class="badge green">connected${s.plan?' · '+esc(s.plan):''}</span>` : '<span class="badge red">not connected</span>';
    $('#ov-login').outerHTML = b.replace('class="badge','id="ov-login" class="badge');
    $('#side-plan').textContent = s.signed_in ? ('ChatGPT: '+(s.plan||'connected')) : 'ChatGPT: not connected';
    if(s.stage==='pending' && s.user_code){ $('#cx-wait').hidden=false; $('#cx-idle').hidden=true; $('#cx-code').textContent=s.user_code; $('#cx-url').href=s.verification_url; if(!pollTimer) pollLogin(); }
    if(s.signed_in){ $('#cx-wait').hidden=true; $('#cx-done').hidden=false; $('#cx-plan').textContent=s.plan||'connected'; $('#cx-acc').textContent=s.account_id?('account '+s.account_id):''; clearInterval(pollTimer); pollTimer=null; }
    const st = await gA('/status').catch(()=>null);
    if(st){ $('#ov-base').textContent=st.base_url; $('#ov-key').textContent=KEY|| '(paste key)'; }
    const u = await gA('/usage').catch(()=>null);
    if(u){ $('#ov-reqs').textContent=u.local.requests; $('#ov-tokens').textContent=(u.local.in_tokens+u.local.out_tokens); $('#ov-errs').textContent=u.local.errors;
      $('#ov-log').innerHTML=(u.local.log||[]).slice(0,8).map(r=>`<tr><td>${fmtT(r.ts)}</td><td>${esc(r.model)}</td><td>${esc(r.tier)}</td><td>${r.status}</td><td>${r.in}/${r.out}</td><td>${r.ms}</td></tr>`).join(''); }
  }catch(e){}
  await loadModels();
  loadGoogleUrl();
  loadAgent();
}

/* ---------- agent setup ---------- */
function loadAgent(){
  const base=location.origin+'/v1';
  const k=KEY||'YOUR_BRIDGE_KEY';
  $('#ag-env').textContent=`export OPENAI_BASE_URL=${base}\nexport OPENAI_API_KEY=${k}`;
  $('#ag-py').textContent=`from openai import OpenAI\nclient = OpenAI(base_url="${base}", api_key="${k}")\nr = client.chat.completions.create(model="gpt-5-codex", messages=[{"role":"user","content":"hi"}])\nprint(r.choices[0].message.content)`;
  $('#ag-curl').textContent=`curl ${base}/chat/completions \\\n  -H "Authorization: Bearer ${k}" -H "Content-Type: application/json" \\\n  -d '{"model":"gpt-5-codex","messages":[{"role":"user","content":"hi"}]}'`;
}
$('#set-save').onclick=async()=>{
  const r=await API.post('/settings',{openai_api_key:$('#set-oai').value.trim()});
  $('#set-out').hidden=false; $('#set-out').textContent=JSON.stringify(r.json);
  toast('Saved (local only)');
};

/* boot: ensure key first, then render */
(async ()=>{ await ensureKey(); refresh();
  setInterval(()=>{ if($('#view-overview').classList.contains('on')||$('#view-usage').classList.contains('on')) refresh(); }, 5000);
})();
