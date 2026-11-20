"""Image-scoped human observations, deliberately separate from training targets."""

OBSERVATION_CHOICES = {
    'condition': ('unknown', 'alive', 'dead'),
    'management': ('unknown', 'pollarded', 'not-pollarded'),
}


def normalize_observations(record):
    result = {}
    for field, choices in OBSERVATION_CHOICES.items():
        if field in record:
            value = record[field]
            if not isinstance(value, str) or value not in choices:
                raise ValueError(f'Invalid tree observation {field}')
            result[field] = value
    if record.get('note') is not None:
        result['note'] = str(record['note'])[:2000]
    return result


OBSERVATIONS_JS = r'''
function observationSelection(sceneId){
  if(selectedEditableTrees(sceneId).length!==1)return null;
  if(deselectedScenes.has(sceneId))return null;
  const regionId=selectedAddedByScene[sceneId];
  if(regionId){
    const region=maskRegions.find(r=>r.region_id===regionId);
    return region?.mode==='confirmed-tree'&&!region.tree_id?{regionId,value:region}:null;
  }
  const ids=[...selectionFor(sceneId)];
  return ids.length===1?{sampleId:ids[0],value:reviews[ids[0]]||{}}:null;
}
function setTreeObservation(sceneId,field,value,editGroup=null){
  const choices={condition:['unknown','alive','dead'],management:['unknown','pollarded','not-pollarded']};
  if(field!=='note'&&!choices[field]?.includes(value))return;
  if(field==='note')value=value.slice(0,2000);
  const selected=observationSelection(sceneId);if(!selected)return;
  if((selected.value[field]??(field==='note'?'':'unknown'))===value)return;
  const previous=moveUndoByScene[sceneId]?.at(-1);
  if(!editGroup||previous?.editGroup!==editGroup||previous.field!==field||previous.sampleId!==selected.sampleId||previous.regionId!==selected.regionId)
    pushReviewUndo(sceneId,{kind:'observation',field,editGroup,sampleId:selected.sampleId,regionId:selected.regionId,
    existed:Object.hasOwn(selected.value,field),before:selected.value[field],
    recordExisted:selected.sampleId?Object.hasOwn(reviews,selected.sampleId):true});
  if(selected.regionId)selected.value[field]=value;
  else reviews[selected.sampleId]={...selected.value,[field]:value};
  persist(sceneId);
}
function renderTreeObservations(card,sceneId){
  const selected=observationSelection(sceneId);
  const selectionKey=selected?(selected.regionId||selected.sampleId):'';
  for(const field of ['condition','management','note']){
    const input=card.querySelector(`[data-observation="${field}"]`);
    input.disabled=!selected;
    // Snapshot/prediction refreshes must not discard a note being typed.
    if(field==='note'&&document.activeElement===input&&input.dataset.selectionKey===selectionKey)continue;
    input.value=selected?(selected.value[field]??(field==='note'?'':'unknown')):(field==='note'?'':'unknown');
    input.dataset.selectionKey=selectionKey;
  }
}
function buildTreeObservations(sceneId){
  const section=document.createElement('section');section.className='tree-observations';
  const disclosure=document.createElement('details');
  const heading=document.createElement('summary');heading.textContent='Notes & observations';
  const help=document.createElement('div');help.className='observation-help';
  help.textContent='For this imagery. Annotation only—does not change species, training, or review status.';
  section.append(disclosure);disclosure.append(heading,help);
  const choices={Condition:[['unknown','Unknown'],['alive','Alive'],['dead','Dead']],
    Management:[['unknown','Unknown'],['pollarded','Pollarded'],['not-pollarded','Not pollarded']]};
  for(const [label,options] of Object.entries(choices)){
    const wrapper=document.createElement('label');wrapper.textContent=label+' ';
    const select=document.createElement('select');select.dataset.observation=label.toLowerCase();
    select.setAttribute('aria-label','Tree '+label.toLowerCase());
    for(const [value,text] of options){const option=document.createElement('option');option.value=value;option.textContent=text;select.append(option);}
    select.addEventListener('change',()=>setTreeObservation(sceneId,select.dataset.observation,select.value));
    wrapper.append(select);disclosure.append(wrapper);
  }
  const note=document.createElement('textarea');note.dataset.observation='note';note.maxLength=2000;
  note.setAttribute('aria-label','Tree notes');note.placeholder='Shadow, stale inventory, merged crowns, systematic shift…';
  let noteEditGroup=null;
  note.addEventListener('focus',()=>{noteEditGroup=Symbol('note edit');});
  note.addEventListener('input',()=>setTreeObservation(sceneId,'note',note.value,noteEditGroup));
  const undo=document.createElement('button');undo.type='button';undo.textContent='Undo last edit';
  undo.title='Shared with movement and classification undo (Ctrl+Z outside text fields)';
  undo.addEventListener('click',()=>undoMove(sceneId));
  undo.dataset.shortcut=/Mac|iPhone|iPad/.test(navigator.platform)?'⌘Z':'Ctrl+Z';
  undo.setAttribute('aria-keyshortcuts','Control+Z Meta+Z');
  disclosure.append(note);section.append(undo);return section;
}
'''
