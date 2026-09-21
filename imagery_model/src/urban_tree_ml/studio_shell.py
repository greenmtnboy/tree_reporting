"""Shared navigation and action styling for every server-rendered Studio screen."""
# ruff: noqa: E501
from __future__ import annotations

import json
import re
from html import escape
from urllib.parse import urlencode
from urban_tree_ml.review_status import inject_review_status_options


def render_studio_shell(html: str, city: str, cities: dict, run_ids: dict) -> str:
    html = inject_review_status_options(html)
    city_query = urlencode({"city": city})
    run = run_ids.get(city)
    model_query = urlencode({"run": run}) if run else city_query
    links = [("/", "Studio", "/?" + city_query),
             ("/registration", "Registration", "/registration?" + city_query),
             ("/model", "Chip review", "/model?" + model_query),
             ("/runs", "Run history", "/runs?" + city_query),
             ("/coverage", "Curation coverage", "/coverage?" + city_query)]
    anchors = "".join(
        f'<a data-page="{path}" href="{escape(url, quote=True)}">{label}</a>'
        for path, label, url in links
    )
    options = "".join(
        f'<option value="{escape(key, quote=True)}"'
        f'{" selected" if key == city else ""}>{escape(label)}</option>'
        for key, label in cities.items()
    )
    nav = (
        '<nav id="studio-navigation" class="studio-nav" aria-label="Studio navigation">'
        f'<div class="studio-links">{anchors}</div>'
        '<label class="studio-city-switch">City '
        f'<select id="studio-city" aria-label="Review city">{options}</select></label></nav>'
    )
    # Legacy templates remain usable as static exports; live pages share this shell.
    html = re.sub(r'<nav\b(?![^>]*aria-label="Review image pages")[^>]*>.*?</nav>', "", html, count=1, flags=re.DOTALL)
    html = html.replace("<body>", "<body>" + nav, 1)
    script = r"""<script>
(() => {
 const nav=document.getElementById('studio-navigation');
 const page=location.pathname.replace(/\/$/,'')||'/';
 nav.querySelectorAll('[data-page]').forEach(a=>{
   if(a.dataset.page===page)a.setAttribute('aria-current','page');
 });
 const runs=__RUNS__;
 const params=new URLSearchParams(location.search);
 // The editor's city owns the data; the return view owns navigation scope.
 let currentCity=params.get('city')==='all'?'all':__CITY__;
 if(page==='/registration'&&params.has('scene')&&params.has('return')){
   try{const source=new URL(params.get('return'),location.origin);
     const scope=source.searchParams.get('city');
     if(source.origin===location.origin&&(scope==='all'||Object.hasOwn(runs,scope)))currentCity=scope;
   }catch{}
 }
 if(['/','/runs','/model','/coverage','/registration'].includes(page)){
   const select=document.getElementById('studio-city');
   select.add(new Option('All cities','all'));
   select.value=currentCity;
 }
 if(currentCity==='all')nav.querySelectorAll('[data-page]').forEach(a=>{
   const target=new URL(a.href,location.origin);target.searchParams.set('city','all');
   target.searchParams.delete('run');if(target.pathname==='/model')target.searchParams.set('split','training-all');
   a.href=target.pathname+target.search;
 });
 if(page==='/')document.querySelectorAll('main a[href]').forEach(a=>{
   const target=new URL(a.href,location.origin);
   if(target.origin===location.origin&&['/registration','/model','/runs','/coverage'].includes(target.pathname)){
     target.searchParams.set('city',currentCity);
     if(currentCity==='all'&&target.pathname==='/model')target.searchParams.set('split','training-all');
     a.href=target.pathname+target.search;
   }
 });
 // Show feedback before the server prepares a curation scene. Keep normal link
 // navigation (and modified clicks) intact, including the validation queue handler.
 let curationLoading=null,curationLoadingTimer=null;
 function resetCurationLoading(){
   clearTimeout(curationLoadingTimer);
   if(!curationLoading)return;
   const {control,label}=curationLoading;
   control.textContent=label;control.removeAttribute('aria-busy');
   control.removeAttribute('aria-disabled');curationLoading=null;
 }
 window.addEventListener('pageshow',resetCurationLoading);
 document.addEventListener('click',event=>{
   if(event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
   const link=event.target.closest('[data-curate-chip],#curate-chip,a[href^="/curate?"] ,a[href^="/curate-training?"]');
   if(!link)return;
   if(curationLoading){event.preventDefault();event.stopImmediatePropagation();return}
   const control=link.querySelector('img')?link.closest('.card')?.querySelector('.action-link'):link;
   if(!control)return;
   curationLoading={control,label:control.textContent};
   control.textContent='Loading curation…';control.setAttribute('aria-busy','true');
   control.setAttribute('aria-disabled','true');
   // A failed/cancelled navigation must leave the action retryable.
   curationLoadingTimer=setTimeout(resetCurationLoading,15000);
 },true);
 // A page tab restores filters, not a transient full-screen curation session.
 // Apply on read too, so URLs saved before this fix are repaired immediately.
 function pageViewUrl(value){const url=new URL(value,location.origin);
   if(url.pathname==='/registration'){
     for(const key of ['fullscreen','scene','return','queue','queue_chip'])url.searchParams.delete(key);
     url.searchParams.set('city',currentCity);
   }
   return url;
 }
 nav.querySelectorAll('[data-page]').forEach(a=>a.addEventListener('click',event=>{
   if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
   try{const last=sessionStorage.getItem('studio-last:'+a.dataset.page+':'+currentCity);
   if(last){const target=pageViewUrl(last);
   if(target.origin===location.origin&&target.pathname===a.dataset.page){event.preventDefault();location.href=target.href}}}catch{}
 }));
 window.addEventListener('pagehide',()=>{try{
   const saved=pageViewUrl(location.href);
   sessionStorage.setItem('studio-last:'+page+':'+currentCity,saved.pathname+saved.search);
 }catch{}});
 document.getElementById('studio-city').addEventListener('change',event=>{
   const city=event.target.value;
   if(page==='/model'){
     const u=new URL(location.href);u.searchParams.set('city',city);
     if(city==='all')u.searchParams.set('split','training-all');
     else {u.searchParams.delete('run');u.searchParams.delete('compare_run');}
     u.searchParams.delete('chip');location.href=u.pathname+u.search;return;
   }
   const route=['/','/registration','/model','/runs','/coverage','/training-queue'].includes(page)?page:'/model';
   const query=new URLSearchParams({city});
   if(route==='/model'&&new URLSearchParams(location.search).get('split')==='train')query.set('split','train');
   if(route==='/model'&&runs[city])query.set('run',runs[city]);
   location.href=route+'?'+query;
 });
 const resize=()=>document.documentElement.style.setProperty('--studio-nav-height',nav.offsetHeight+'px');
 new ResizeObserver(resize).observe(nav);resize();
 const backupBadge=document.createElement('span');backupBadge.id='backup-health';
 backupBadge.style.cssText='font-size:11px;color:#c3d6ca;max-width:460px';nav.append(backupBadge);
 const backupButton=document.createElement('button');backupButton.type='button';
 backupButton.textContent='Back up now';backupButton.title='Back up saved SF + Boston annotations and imagery privately. Does not publish for training.';
 nav.append(backupButton);
 backupButton.addEventListener('click',async()=>{
   backupButton.disabled=true;backupButton.textContent='Starting backup…';
   try{const response=await fetch('/api/backup?'+new URLSearchParams({city:currentCity==='all'?Object.keys(runs)[0]:currentCity}),
     {method:'POST',headers:{'X-Studio-Backup':'1'}});
     const result=await response.json();if(!response.ok)throw new Error(result.error||'Backup failed');
     await backupHealth();
   }catch(error){backupBadge.textContent=error.message;backupButton.disabled=false;backupButton.textContent='Back up now';}
 });
 async function backupHealth(){try{
   if(currentCity==='all'){
     const states=await Promise.all(Object.keys(runs).map(async city=>{
       const response=await fetch('/api/backup-status?'+new URLSearchParams({city}));
       if(!response.ok)return {city,error:true};return {city,...await response.json()};
     }));
     const busy=states.some(s=>s.job?.running);backupButton.disabled=busy;backupButton.textContent=busy?'Backing up…':'Back up all cities';
     backupBadge.textContent=states.map(s=>s.city.toUpperCase()+': '+(s.error?'status unavailable':
       (s.snapshot?.error?'snapshot failed':s.snapshot?.pending?'snapshot pending':'saved')+' · '+
       (s.git?.current&&s.remote?.current?'backed up':'backup pending')+' · '+(s.published_for_training?'published':'draft')+
       (s.job?.error?' · backup failed':'')+(s.archive_error?' · archive error':''))).join(' | ');
     return;
   }
   const response=await fetch('/api/backup-status?'+new URLSearchParams({city:currentCity}));
   if(!response.ok)throw new Error('Backup status unavailable');const s=await response.json();
   backupButton.disabled=!!s.job?.running;backupButton.textContent=s.job?.running?'Backing up…':'Back up now';
   backupBadge.textContent='Saved locally'+(s.snapshot?.error?' · Snapshot failed':s.snapshot?.pending?' · Snapshot pending':' · Snapshot ready')+' · Git '+(s.git.current?'backed up':'pending')+
     ' · Images + labels '+(s.remote.current?'backed up':'pending')+
     ' · Training '+(s.published_for_training?'published':'draft')+(s.archive_error?' · ARCHIVE ERROR':'')+(s.job?.error?' · Backup failed: '+s.job.error:'');
   backupBadge.title=s.archive_error||('Last private backup: '+(s.remote.at||'not completed')+
     '; last Git backup: '+(s.git.at||'not completed'));
 }catch{backupBadge.textContent='Backup status unavailable';}}
 backupHealth();setInterval(backupHealth,15000);
})();
</script>""".replace("__RUNS__", json.dumps(run_ids).replace("<", "\\u003c")).replace(
        "__CITY__", json.dumps(city).replace("<", "\\u003c")
    )
    return html.replace("</head>", SHARED_STYLE + VIEW_STATE + "</head>", 1).replace(
        "</body>", script + "</body>", 1
    )


VIEW_STATE = """<script>
window.studioViewState=(()=>{
 const path=location.pathname.replace(/\\/$/,'')||'/',params=new URLSearchParams(location.search);
 const ids={ '/model':['radius','threshold','sort','limit','review-status','assignment'],
 '/registration':['split-filter','coverage-filter','status-filter','scene-status-filter','show-stacks'],
 '/coverage':['city','status','split','sort','search'], '/runs':['dataset','radius'],
 '/compare':['radius','chip']}[path]||[];
 const scope=params.get('run')||params.get('city')||'default';
 const key='studio-view-v1:'+path+':'+scope;
 let saved={};try{saved=JSON.parse(localStorage.getItem(key)||'{}')}catch{}
 const orderKey='studio-chip-review-order';
 let savedOrder=null;try{if(path==='/model')savedOrder=localStorage.getItem(orderKey)}catch{}
 function restore(){for(const id of ids){const el=document.getElementById(id);
 if(!el)continue;const value=params.has(id)?params.get(id):(id==='sort'&&path==='/model'?savedOrder??saved[id]:saved[id]);if(value==null)continue;
 if(el.type==='checkbox')el.checked=value==='1';
 else if(el.tagName!=='SELECT'||[...el.options].some(o=>o.value===value))el.value=value;
 }
 // Curation queues capture this URL. Include the restored order before building them.
 if(path==='/model'&&document.getElementById('sort')){
   const url=new URL(location.href);url.searchParams.set('sort',document.getElementById('sort').value);
   history.replaceState(null,'',url);
 }}
 function save(){for(const id of ids){const el=document.getElementById(id);if(el)saved[id]=el.type==='checkbox'?(el.checked?'1':'0'):el.value}
 try{localStorage.setItem(key,JSON.stringify(saved))}catch{}
 const url=new URL(location.href);for(const id of ids)if(saved[id]!=null)url.searchParams.set(id,saved[id]);
 history.replaceState(null,'',url);
 }
 function changed(e){if(!ids.includes(e.target.id))return;
   if(path==='/model'&&e.target.id==='sort'){
     savedOrder=e.target.value;try{localStorage.setItem(orderKey,savedOrder)}catch{}
   }
   save();
 }
 document.addEventListener('input',changed);
 document.addEventListener('change',changed);
 return {restore,save};
})();
</script>"""


SHARED_STYLE = """<style>
:root{--studio-nav-height:61px}
#studio-navigation{position:sticky;top:0;z-index:250;display:flex;flex-wrap:wrap;
 align-items:center;gap:12px;padding:10px 24px;margin:0;background:#0b100df5;
 border:0;border-bottom:1px solid #33453a;box-sizing:border-box;font:14px system-ui}
#studio-navigation .studio-links{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
#studio-navigation a,body .action,body .action-link,
body .controls button,body header button,body .card-head button,
body .actions button,body .mask-actions button,body .modal-head button{
 display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;
 min-height:36px;padding:7px 12px;border-radius:6px;font:inherit;line-height:20px;
 text-decoration:none;cursor:pointer;vertical-align:middle}
#studio-navigation a,body .action,body .action-link{
 background:#203027;color:#edf6ef;border:1px solid #496252}
#studio-navigation a:hover,body .action:hover,body .action-link:hover{background:#2b4233}
#studio-navigation a[aria-current="page"]{border-color:#83c894;background:#304e39;color:#fff}
#studio-navigation .studio-city-switch{display:inline-flex;align-items:center;gap:8px;
 margin-left:auto;color:#b9cabe;font:14px system-ui}
#studio-navigation select{min-height:36px;padding:7px 12px;border-radius:6px;
 background:#203027;color:#edf6ef;border:1px solid #496252;font:inherit}
body button:disabled{cursor:default;opacity:.5}
#studio-navigation a:focus-visible,body button:focus-visible,body .action:focus-visible,
body .action-link:focus-visible{outline:2px solid #99dbac;outline-offset:3px}
body button[hidden],body .action[hidden],body .action-link[hidden]{display:none!important}
#studio-navigation~header{top:var(--studio-nav-height)}
body .card.fullscreen{top:var(--studio-nav-height)}
body .modal{top:var(--studio-nav-height)}
@media(max-width:720px){#studio-navigation{padding:10px 12px}
 #studio-navigation .studio-city-switch{margin-left:0}}
</style>"""
