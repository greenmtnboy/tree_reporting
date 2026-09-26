import json
import subprocess

from urban_tree_ml.studio_shell import VIEW_STATE


def test_order_persists_across_runs_and_is_in_curation_return_url():
    script = VIEW_STATE.strip().removeprefix('<script>').removesuffix('</script>')
    harness = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const values=new Map();
function page(query){
 const url=new URL('http://localhost/model'+query),listeners={};
 const sort={id:'sort',tagName:'SELECT',value:'worst',options:['worst','missed','matched'].map(value=>({value}))};
 const context={URL,URLSearchParams,window:{},location:{pathname:url.pathname,search:url.search,href:url.href},
 localStorage:{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v)},
 document:{getElementById:id=>id==='sort'?sort:null,addEventListener:(name,fn)=>listeners[name]=fn}};
 context.history={replaceState:(_,__,u)=>{context.location.href=String(u);context.location.search=new URL(u).search}};
 vm.runInNewContext(SCRIPT,context);context.window.studioViewState.restore();
 return {context,sort,change(value){sort.value=value;listeners.change({target:sort})}};
}
const a=page('?run=v2&city=all');a.change('missed');
const b=page('?run=v3&city=usbos');assert.equal(b.sort.value,'missed');
assert.equal(new URL(b.context.location.href).searchParams.get('sort'),'missed');
// Explicit links win; opening one doesn't erase the user's chosen default.
const c=page('?run=v3&sort=matched');assert.equal(c.sort.value,'matched');
assert.equal(page('?run=v4').sort.value,'missed');
values.set('studio-chip-review-order','invalid');
assert.equal(page('?run=v5').sort.value,'worst');
'''
    subprocess.run(['node', '-'], input='const SCRIPT='+json.dumps(script)+';\n'+harness,
                   encoding='utf-8', check=True, capture_output=True)
