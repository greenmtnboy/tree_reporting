"""Shared navigation and action styling for every server-rendered Studio screen."""
# ruff: noqa: E501
from __future__ import annotations

import json
import re
from html import escape
from urllib.parse import urlencode


def render_studio_shell(html: str, city: str, cities: dict, run_ids: dict) -> str:
    city_query = urlencode({"city": city})
    run = run_ids.get(city)
    model_query = urlencode({"run": run}) if run else city_query
    links = [("/", "Studio", "/?" + city_query),
             ("/registration", "Registration", "/registration?" + city_query),
             ("/model", "Model validation", "/model?" + model_query),
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
    html = re.sub(r"<nav\b[^>]*>.*?</nav>", "", html, count=1, flags=re.DOTALL)
    html = html.replace("<body>", "<body>" + nav, 1)
    script = r"""<script>
(() => {
 const nav=document.getElementById('studio-navigation');
 const page=location.pathname.replace(/\/$/,'')||'/';
 nav.querySelectorAll('[data-page]').forEach(a=>{
   if(a.dataset.page===page)a.setAttribute('aria-current','page');
 });
 const runs=__RUNS__;
 const currentCity=__CITY__;
 nav.querySelectorAll('[data-page]').forEach(a=>a.addEventListener('click',event=>{
   if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
   try{const last=sessionStorage.getItem('studio-last:'+a.dataset.page+':'+currentCity);
   if(last){const target=new URL(last,location.origin);
   if(target.origin===location.origin&&target.pathname===a.dataset.page){event.preventDefault();location.href=target.href}}}catch{}
 }));
 window.addEventListener('pagehide',()=>{try{
   sessionStorage.setItem('studio-last:'+page+':'+currentCity,location.pathname+location.search);
 }catch{}});
 document.getElementById('studio-city').addEventListener('change',event=>{
   const city=event.target.value;
   const route=['/','/registration','/model','/runs','/coverage'].includes(page)?page:'/model';
   const query=new URLSearchParams({city});
   if(route==='/model'&&runs[city])query.set('run',runs[city]);
   location.href=route+'?'+query;
 });
 const resize=()=>document.documentElement.style.setProperty('--studio-nav-height',nav.offsetHeight+'px');
 new ResizeObserver(resize).observe(nav);resize();
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
 const ids={ '/model':['radius','threshold','sort','limit','unreviewed'],
 '/registration':['split-filter','coverage-filter','status-filter','scene-status-filter','show-stacks'],
 '/coverage':['city','status','split','sort','search'], '/runs':['dataset','radius'],
 '/compare':['radius','chip']}[path]||[];
 const scope=params.get('run')||params.get('city')||'default';
 const key='studio-view-v1:'+path+':'+scope;
 let saved={};try{saved=JSON.parse(localStorage.getItem(key)||'{}')}catch{}
 function restore(){for(const id of ids){const el=document.getElementById(id);
 if(!el)continue;const value=params.has(id)?params.get(id):saved[id];if(value==null)continue;
 if(el.type==='checkbox')el.checked=value==='1';
 else if(el.tagName!=='SELECT'||[...el.options].some(o=>o.value===value))el.value=value;
 }}
 function save(){for(const id of ids){const el=document.getElementById(id);if(el)saved[id]=el.type==='checkbox'?(el.checked?'1':'0'):el.value}
 try{localStorage.setItem(key,JSON.stringify(saved))}catch{}
 const url=new URL(location.href);for(const id of ids)if(saved[id]!=null)url.searchParams.set(id,saved[id]);
 history.replaceState(null,'',url);
 }
 document.addEventListener('input',e=>{if(ids.includes(e.target.id))save()});
 document.addEventListener('change',e=>{if(ids.includes(e.target.id))save()});
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
