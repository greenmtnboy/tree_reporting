"""On-demand prediction comparison without leaving the annotation editor."""

COMPARISON_PREFERENCE_JS = r"""
function comparisonKeyForRun(run) { return 'tree-review:comparison:' + (run || '').split('::')[0]; }
function savedComparison(run) {
  try { return localStorage.getItem(comparisonKeyForRun(run)) || ''; } catch { return ''; }
}
function rememberComparison(run, other) {
  try { localStorage.setItem(comparisonKeyForRun(run), other); } catch {}
}
function comparisonParameters(run, explicit) {
  const params = new URLSearchParams({run,compare_run:explicit || 'auto'});
  const saved = savedComparison(run);
  if (params.get('compare_run')==='auto' && saved) params.set('preferred_run',saved);
  return params;
}
"""

CURATION_COMPARE_JS = r"""
let curationComparisonEnabled = localStorage.getItem('tree-review:compare-enabled') !== '0';
function predictionCrownPercent(prediction, display) {
  const radius = Number(prediction.crown_radius_m);
  const resolution = Number(display.resolution_m), pixels = Number(display.chip_pixels);
  if (![radius,resolution,pixels].every(v => Number.isFinite(v) && v > 0)) return null;
  return 100 * 2 * radius / (resolution * pixels);
}
function persistCurationModels(run, compareRun) {
  predictionRun = run;
  persistCurationView({run,compare_run:compareRun});
  scheduleQueuePrefetch();
}
function persistCurationView(values) {
  // Preserve filters written by the shared header since this editor opened.
  const url = new URL(location.href);
  for (const [key,value] of Object.entries(values)) {
    pageParameters.set(key,String(value));url.searchParams.set(key,String(value));
  }
  if (curationReturn) {
    const back = new URL(curationReturn, location.origin);
    if (back.origin === location.origin) {
      for(const [key,value] of Object.entries(values))back.searchParams.set(key,String(value));
      curationReturn = back.pathname + back.search + back.hash;
      pageParameters.set('return', curationReturn);url.searchParams.set('return',curationReturn);
    }
  }
  history.replaceState(null, '', url.pathname + url.search + url.hash);
  document.querySelectorAll('.card').forEach(card => {
    delete card.dataset.predictionsLoaded;
  });
}
function persistCurationConfidence(input) {
  const value=Number(input.value);
  if(!input.value.trim() || !Number.isFinite(value) || value<0 || value>1)return false;
  requestedPredictionThreshold=value;
  persistCurationView({threshold:value});
  // Prediction payloads contain all confidences: no refetch/prefetch needed.
  return true;
}
async function toggleCurationCompare(card, scene, button) {
  if (button.disabled) return;
  card.querySelectorAll('.curation-comparison-error').forEach(el => el.remove());
  if (card._comparisonPanel) {
    card._comparisonPanel.hidden = !card._comparisonPanel.hidden;
    curationComparisonEnabled = !card._comparisonPanel.hidden;
    localStorage.setItem('tree-review:compare-enabled', curationComparisonEnabled ? '1' : '0');
    card.classList.toggle('comparing-models', !card._comparisonPanel.hidden);
    button.setAttribute('aria-pressed', String(!card._comparisonPanel.hidden));
    if(!curationComparisonEnabled)void loadPredictionOverlay(card,scene);
    scheduleQueuePrefetch();
    return;
  }
  curationComparisonEnabled = true;
  localStorage.setItem('tree-review:compare-enabled','1');
  // Reserve comparison mode before awaiting the response: never flash the single-model layer.
  card.classList.add('comparing-models');
  const badge=card.querySelector('.prediction-badge');
  if(badge)badge.textContent='Loading comparison…';
  button.disabled = true; button.textContent = 'Loading models…';
  try {
    const response = await fetchCurationAsset('/api/runs/chip/' + encodeURIComponent(scene.validation_chip_id)
      + '?' + comparisonParameters(predictionRun, pageParameters.get('compare_run')));
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'Comparison unavailable');
    const available = payload.runs.filter(e => e.available);
    let current = available.find(e => e.run.run_id === payload.selected_run_id);
    if (!current) throw new Error('Current model has no predictions for this chip');
    const choices = payload.choices;
    if (choices.length<2) throw new Error('No compatible comparison model');
    const panel = document.createElement('div'); panel.className = 'curation-compare-panel fullscreen-only';

    const select = document.createElement('select'); select.setAttribute('aria-label', 'Comparison model');
    const currentSelect = document.createElement('select'); currentSelect.setAttribute('aria-label', 'Current model');
    select.style.maxWidth = 'min(380px,80vw)';
    currentSelect.style.maxWidth = select.style.maxWidth;
    choices.forEach(run => {
      const option = document.createElement('option'); option.value = run.run_id;
      option.textContent = run.training_run_id || run.run_id; select.append(option);
      const currentOption=document.createElement('option');currentOption.value=run.run_id;
      currentOption.textContent=option.textContent;currentSelect.append(currentOption);
    });
    currentSelect.value=payload.selected_run_id;
    select.value = payload.comparison_run_id || '';
    if (!select.value) {
      select.add(new Option('Choose a comparison model', '', true, true), 0);
    }
    const layerToggle = (label, color) => {
      const wrap = document.createElement('label'); wrap.style.color = color;
      const input = document.createElement('input'); input.type = 'checkbox'; input.checked = true;
      wrap.append(input, document.createTextNode(label)); panel.append(wrap); return input;
    };
    const showCurrent = layerToggle('Current / orange ', '#ffae39');
    panel.append(currentSelect);
    const showOther = layerToggle('Compare / purple ', '#b79aff'); panel.append(select);
    const confidenceLabel = document.createElement('label'); confidenceLabel.textContent = ' Confidence ';
    const confidence = document.createElement('input'); confidence.type = 'number'; confidence.min = '0';
    confidence.max = '1'; confidence.step = '0.05'; confidence.style.width = '70px';
    confidence.value = String(Number.isFinite(requestedPredictionThreshold) ? requestedPredictionThreshold : current.data.confidence_threshold ?? .35);
    confidenceLabel.append(confidence); panel.append(confidenceLabel);
    const note = document.createElement('div'); note.style.fontSize = '12px'; panel.append(note);
    const render = () => {
      const wrap = card.querySelector('.image-wrap');
      wrap.querySelectorAll('.curation-comparison-ring').forEach(el => el.remove());
      const other = available.find(e => e.run.run_id === select.value);
      current = available.find(e => e.run.run_id === currentSelect.value);
      if (!other && !current) { note.textContent = 'Neither selected model has saved predictions for this chip.'; if(badge)badge.textContent='Predictions unavailable'; return; }
      if (other && current && Math.abs(current.display.chip_pixels*current.display.resolution_m-other.display.chip_pixels*other.display.resolution_m)>1e-6) {
        note.textContent = 'Image scales differ; comparison cannot be overlaid safely.'; return;
      }
      const threshold = Math.max(0, Math.min(1, Number(confidence.value)));
      if (!Number.isFinite(threshold)) { note.textContent = 'Enter confidence between 0 and 1.'; return; }
      for (const [entry, show, color, size] of [[other,showOther.checked,'rgba(183,154,255,0.65)',18],[current,showCurrent.checked,'#ffae39',12]]) {
        if (!entry || !show) continue;
        const scale = 100 * entry.display.output_stride / entry.display.chip_pixels;
        const radius=curationMatchRadius(),matches=frozenPredictionMatches(entry.data,entry.display,threshold,radius);
        for (const p of entry.data.predictions.filter(p => p.score >= threshold)) {
          // Actual crown-head output only: do not present DBH estimates as model geometry.
          const diameter = predictionCrownPercent(p, entry.display);
          if (diameter !== null) {
            const crown = document.createElement('i');
            crown.className = 'curation-comparison-ring comparison-crown';
            crown.style.cssText = `position:absolute;pointer-events:none;z-index:3;border:1px solid ${color};border-radius:50%;background:transparent;width:${diameter}%;height:${diameter}%;box-sizing:border-box;transform:translate(-50%,-50%);left:${p.output_x*scale}%;top:${p.output_y*scale}%`;
            wrap.append(crown);
          }
          const ring = document.createElement('i'); ring.className = 'curation-comparison-ring';
          ring.style.cssText = `position:absolute;pointer-events:none;z-index:4;border:2px solid ${color};border-radius:50%;background:transparent;width:${size}px;height:${size}px;box-sizing:border-box;transform:translate(-50%,-50%);left:${p.output_x*scale}%;top:${p.output_y*scale}%`;
          ring.style.color=color;
          ring.title=`${entry.run.training_run_id||entry.run.run_id} · ${(100*p.score).toFixed(0)}% center confidence`;
          bindPredictionAcceptance(ring,card,scene,p,entry.display,entry.run.run_id);
          decoratePredictionMatch(ring,matches.get(p),radius);
          wrap.append(ring);
        }
      }
      const count = entry => entry ? entry.data.predictions.filter(p => p.score >= threshold).length : 'unavailable';
      note.textContent = `Orange: ${count(current)} · Purple: ${count(other)}`;
      note.title = `Prediction counts at the selected confidence. Thin rings show crowns. Filled centers match frozen labels within ${curationMatchRadius()} m; hollow centers are unmatched and dashed centers lack match data.`;
      enablePredictionMatchHover(card);
      if(badge){badge.textContent=`Comparison: ${count(current)} current · ${count(other)} other`;badge.title=note.textContent;}
    };
    let loadSequence = 0;
    const loadPair = async () => {
      const sequence = ++loadSequence, id = select.value;
      if (!id) return;
      persistCurationModels(currentSelect.value, id);
      if (available.some(e => e.run.run_id === id) && available.some(e => e.run.run_id === currentSelect.value)) { render(); return; }
      card.querySelectorAll('.curation-comparison-ring').forEach(el => el.remove());
      note.textContent = 'Loading selected model for this chip…';
      try {
        const response = await fetch('/api/runs/chip/' + encodeURIComponent(scene.validation_chip_id)
          + '?' + new URLSearchParams({run:currentSelect.value,compare_run:id}));
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Could not load model');
        for (const entry of result.runs.filter(e => e.available)) {
          if (!available.some(e => e.run.run_id === entry.run.run_id)) available.push(entry);
        }
        if (sequence === loadSequence) render();
      } catch (error) { if (sequence === loadSequence) note.textContent = error.message; }
    };
    select.addEventListener('change', () => {
      if (select.value) rememberComparison(currentSelect.value, select.value);
      void loadPair();
    });
    currentSelect.addEventListener('change', async () => {
      const run = currentSelect.value, sequence = ++loadSequence;
      persistCurationModels(run, 'auto');
      try {
        const response = await fetch('/api/runs/chip/' + encodeURIComponent(scene.validation_chip_id)
          + '?' + comparisonParameters(run, 'auto'));
        const result = await response.json();
        if (sequence !== loadSequence) return;
        if (!response.ok) throw new Error(result.error || 'Could not load model');
        for (const entry of result.runs.filter(e => e.available)) {
          if (!available.some(e => e.run.run_id === entry.run.run_id)) available.push(entry);
        }
        select.value = result.comparison_run_id || '';
        for (const option of select.options) option.toggleAttribute('selected',option.value === select.value);
        render();
      } catch (error) { if (sequence === loadSequence) note.textContent = error.message; }
    });
    for (const input of [showCurrent,showOther]) input.addEventListener('change', render);
    const changeConfidence=()=>{
      if(persistCurationConfidence(confidence))render();
      else note.textContent='Enter confidence between 0 and 1.';
    };
    confidence.addEventListener('input',changeConfidence);
    confidence.addEventListener('change',changeConfidence);
    card._comparisonPanel = panel; button.parentElement.append(panel);
    card.classList.add('comparing-models'); button.setAttribute('aria-pressed','true'); render();
  } catch (error) {
    button.title = error.message; button.textContent = 'Retry comparison';
    button.setAttribute('aria-pressed', 'false');
    // A render error can occur after attaching the panel. Do not cache a broken panel.
    card._comparisonPanel?.remove();
    delete card._comparisonPanel;
    card.querySelectorAll('.curation-comparison-ring').forEach(el => el.remove());
    card.classList.remove('comparing-models');
    const message = document.createElement('div');
    message.className = 'curation-comparison-error';
    message.setAttribute('role', 'status');
    message.textContent = 'Comparison unavailable: ' + error.message + '. You can retry comparison.';
    button.parentElement.append(message);
    if (badge) badge.textContent = 'Comparison unavailable — loading current predictions…';
    // Only fetch the single-model overlay if the comparison could not be loaded.
    await loadPredictionOverlay(card,scene);
    // The overlay may already be cached (or loading). Never leave the comparison loading badge stale.
    if (badge) {
      badge.textContent = card.dataset.predictionsLoaded === 'true'
        ? 'Comparison unavailable — showing current predictions'
        : card.dataset.predictionsLoaded === 'loading'
          ? 'Comparison unavailable — loading current predictions…'
          : 'Predictions unavailable — retry comparison';
      badge.title = message.textContent;
    }
    return;
  } finally { button.disabled = false; scheduleQueuePrefetch(); }
  button.textContent = 'Compare models';
  button.title = 'Overlay another model on this image while curating';
}
"""
