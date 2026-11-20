"""Browser-only aerial display preferences; never part of annotation state."""

AERIAL_DISPLAY_CSS = """
.aerial-display { position: relative; align-self: start; text-align: left; }
.aerial-display summary { cursor: pointer; padding: 5px 8px; border: 1px solid #496252;
  border-radius: 7px; font-size: 12px; background: #203027; }
.aerial-display[data-active=true] summary { border-color: #dbcc81; color: #fff0ac; }
.aerial-display-panel { position: absolute; right: 0; top: 100%; z-index: 30;
  width: min(310px, 85vw); padding: 12px; background: #17251f; border: 1px solid #62786a;
  border-radius: 8px; box-shadow: 0 8px 24px #0009; font-size: 12px; }
.aerial-display-panel label { display: flex; align-items: center; gap: 8px; margin: 9px 0; }
.aerial-display-panel input[type=range] { width: 125px; min-width: 0; flex: 1; }
.aerial-display-panel output { width: 42px; text-align: right; font-variant-numeric: tabular-nums; }
.aerial-display-panel p { color: #b7c8bc; margin: 8px 0 0; line-height: 1.4; }
.card .image-wrap > img,
.card .pixel-zoom canvas { filter: var(--aerial-display-filter, none); }
"""

AERIAL_DISPLAY_SVG = """
<svg width="0" height="0" aria-hidden="true" style="position:absolute;pointer-events:none">
  <defs><filter id="aerial-display-sharpen" color-interpolation-filters="sRGB"
    x="0" y="0" width="100%" height="100%">
    <feConvolveMatrix id="aerial-display-kernel" order="3" kernelMatrix="0 0 0 0 1 0 0 0 0"
      divisor="1" bias="0" edgeMode="duplicate" preserveAlpha="true"/>
  </filter></defs>
</svg>
"""

AERIAL_DISPLAY_JS = r"""
const aerialDisplay = (() => {
  const defaults = {brightness:100, contrast:100, sharpen:0, enabled:true};
  const limits = {brightness:[50,250], contrast:[50,200], sharpen:[0,100]};
  const storageKey = 'tree-review:aerial-display:v1:' + (pageParameters.get('city') || metadata.city || 'default');
  function sanitize(value) {
    const result = {...defaults};
    for (const [key,[low,high]] of Object.entries(limits)) {
      if (typeof value?.[key] === 'number' && Number.isFinite(value[key]))
        result[key] = Math.round(Math.max(low,Math.min(high,value[key])));
    }
    if (typeof value?.enabled === 'boolean') result.enabled = value.enabled;
    return result;
  }
  let state;
  try { state = sanitize(JSON.parse(localStorage.getItem(storageKey))); }
  catch { state = {...defaults}; }
  function syncPanel(panel) {
    panel.dataset.active = String(state.enabled && (state.brightness!==100 || state.contrast!==100 || state.sharpen!==0));
    panel.querySelector('[data-display-enabled]').checked = state.enabled;
    for (const key of Object.keys(limits)) {
      panel.querySelector(`[data-display-setting="${key}"]`).value = state[key];
      panel.querySelector(`[data-display-value="${key}"]`).textContent = state[key]+'%';
    }
  }
  function apply(save=false) {
    const strength=state.sharpen/100;
    document.getElementById('aerial-display-kernel').setAttribute('kernelMatrix',
      `0 ${-strength} 0 ${-strength} ${1+4*strength} ${-strength} 0 ${-strength} 0`);
    const filter=state.enabled
      ? `brightness(${state.brightness/100}) contrast(${state.contrast/100})` +
        (state.sharpen ? ' url("#aerial-display-sharpen")' : '') : 'none';
    document.documentElement.style.setProperty('--aerial-display-filter',filter);
    document.querySelectorAll('.aerial-display').forEach(syncPanel);
    if (save) { try { localStorage.setItem(storageKey,JSON.stringify(state)); } catch {} }
  }
  function controls() {
    const panel=document.createElement('details'); panel.className='aerial-display';
    panel.innerHTML=`<summary>Aerial display</summary><div class="aerial-display-panel">
      <label><input type="checkbox" data-display-enabled> Apply display filters</label>
      <label>Brightness <input type="range" min="50" max="250" step="5" aria-label="Aerial brightness"
        data-display-setting="brightness"><output data-display-value="brightness"></output></label>
      <label>Contrast <input type="range" min="50" max="200" step="5" aria-label="Aerial contrast"
        data-display-setting="contrast"><output data-display-value="contrast"></output></label>
      <label>Sharpen <input type="range" min="0" max="100" step="5" aria-label="Aerial sharpening"
        data-display-setting="sharpen"><output data-display-value="sharpen"></output></label>
      <button type="button" data-display-preset>Lift shadows</button>
      <button type="button" data-display-reset>Reset</button>
      <p>Display only · remembered for this city. Labels, predictions, Street View, and training pixels are unchanged.
      Pixel close-up uses these settings too. Sharpening can create halos; disable filters to check the original.</p></div>`;
    panel.addEventListener('input',event=>{
      const key=event.target.dataset.displaySetting;
      if (!Object.hasOwn(limits,key)) return;
      state=sanitize({...state,[key]:Number(event.target.value),enabled:true}); apply(true);
    });
    panel.querySelector('[data-display-enabled]').addEventListener('change',event=>{
      state.enabled=event.target.checked; apply(true);
    });
    panel.querySelector('[data-display-preset]').addEventListener('click',()=>{
      state={brightness:150,contrast:85,sharpen:0,enabled:true}; apply(true);
    });
    panel.querySelector('[data-display-reset]').addEventListener('click',()=>{
      state={...defaults}; apply(true);
    });
    // Don't let filter sliders/buttons trigger curation hotkeys or scene navigation.
    panel.addEventListener('keydown',event=>{
      if(event.key==='Escape') { panel.open=false; panel.querySelector('summary').focus(); }
      event.stopPropagation();
    });
    panel.addEventListener('toggle',()=>{
      if(panel.open) document.querySelectorAll('.aerial-display').forEach(other=>{
        if(other!==panel) other.open=false;
      });
    });
    syncPanel(panel); return panel;
  }
  apply();
  return {controls};
})();
"""
