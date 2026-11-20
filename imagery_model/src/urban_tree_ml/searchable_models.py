"""Shared searchable model picker; native selects remain the state/event contract."""

SEARCHABLE_MODELS = r"""<style>
.model-picker{display:inline-block;position:relative;width:min(380px,80vw);vertical-align:middle}
.model-picker input{box-sizing:border-box;width:100%;min-height:36px}
.model-picker-list{position:absolute;left:0;right:0;top:100%;z-index:10000;max-height:260px;overflow:auto;background:#17251e;border:1px solid #648771;border-radius:6px;box-shadow:0 6px 18px #0008}
.model-picker-list [role=option]{padding:9px 10px;cursor:pointer;overflow-wrap:anywhere;color:#e7f3eb}
.model-picker-list [aria-selected=true]{background:#355340}
.model-picker-empty{padding:10px;color:#b5cabc}
</style><script>
(() => {
let sequence=0;
const selector='#review-model,#overlay-a,#overlay-b,select[aria-label="Current model"],select[aria-label="Comparison model"]';
function modelMatches(options, query){
 const terms=query.toLowerCase().trim().split(/\s+/).filter(Boolean);
 return options.filter(o=>!o.disabled&&terms.every(t=>(o.textContent+' '+o.value).toLowerCase().includes(t)));
}
function enhance(select){
 if(select.dataset.searchableModel)return;
 select.dataset.searchableModel='1';
 const wrap=document.createElement('span');wrap.className='model-picker';
 const input=document.createElement('input');input.type='search';input.autocomplete='off';
 input.placeholder='Search models…';input.setAttribute('aria-label',select.getAttribute('aria-label')||'Model');
 input.setAttribute('role','combobox');input.setAttribute('aria-autocomplete','list');
 input.setAttribute('aria-expanded','false');
 const list=document.createElement('div');list.id='model-options-'+(++sequence);
 list.className='model-picker-list';list.setAttribute('role','listbox');list.hidden=true;
 input.setAttribute('aria-controls',list.id);
 select.after(wrap);wrap.append(input,list);select.hidden=true;
 let matches=[],active=0;
 const sync=()=>{
   const name=select.selectedOptions[0]?.textContent||'';
   const version=name.match(/(?:^|-)v\d+(?:-|$)/);
   input.value=version && select.closest('.curation-compare-panel')
     ? name.slice(version.index).replace(/^-/, '').replaceAll('-', ' ') : name;
   input.title=name;input.disabled=select.disabled;
 };
 const close=()=>{list.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');sync();};
 const choose=option=>{const changed=select.value!==option.value;select.value=option.value;close();input.blur();if(changed)select.dispatchEvent(new Event('change',{bubbles:true}));};
 const highlight=()=>{Array.from(list.children).forEach((node,i)=>node.setAttribute('aria-selected',String(i===active)));if(matches.length){input.setAttribute('aria-activedescendant',list.children[active].id);list.children[active].scrollIntoView({block:'nearest'});}};
 const render=()=>{
   matches=modelMatches(Array.from(select.options),input.value);active=0;list.replaceChildren();
   for(const [i,option] of matches.entries()){
     const row=document.createElement('div');row.id=list.id+'-'+i;row.setAttribute('role','option');row.textContent=option.textContent;row.title=option.value;
     row.addEventListener('pointerdown',event=>event.preventDefault());row.addEventListener('click',()=>choose(option));list.append(row);
   }
   if(!matches.length){input.removeAttribute('aria-activedescendant');const empty=document.createElement('div');empty.className='model-picker-empty';empty.textContent='No matching models';list.append(empty);}
   list.hidden=false;input.setAttribute('aria-expanded','true');highlight();
 };
 input.addEventListener('focus',()=>{input.value='';render();});
 input.addEventListener('input',render);
 input.addEventListener('blur',close);
 input.addEventListener('keydown',event=>{
   // Never let picker keystrokes trigger curation shortcuts or form submission.
   event.stopPropagation();
   if(event.key==='Escape'){event.preventDefault();close();input.blur();}
   else if(event.key==='Enter'){event.preventDefault();if(!list.hidden&&matches[active])choose(matches[active]);}
   else if(event.key==='ArrowDown'||event.key==='ArrowUp'){
     event.preventDefault();if(list.hidden)render();
     else if(matches.length){active=(active+(event.key==='ArrowDown'?1:-1)+matches.length)%matches.length;highlight();}
   }else if(event.key==='Tab')close();
 });
 select.addEventListener('change',sync);
 new MutationObserver(()=>{if(document.activeElement===input)render();else sync();}).observe(select,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','selected']});
 sync();
}
document.querySelectorAll(selector).forEach(enhance);
new MutationObserver(records=>{
 for(const record of records)for(const node of record.addedNodes){
   if(node.nodeType!==1)continue;
   if(node.matches(selector))enhance(node);
   node.querySelectorAll(selector).forEach(enhance);
 }
}).observe(document.body,{childList:true,subtree:true});
})();
</script>"""
