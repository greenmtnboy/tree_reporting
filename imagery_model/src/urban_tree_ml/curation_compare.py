"""On-demand prediction comparison without leaving the annotation editor."""

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
  pageParameters.set('run', run);
  pageParameters.set('compare_run', compareRun);
  if (curationReturn) {
    const back = new URL(curationReturn, location.origin);
    if (back.origin === location.origin) {
      back.searchParams.set('run', run);
      back.searchParams.set('compare_run', compareRun);
      curationReturn = back.pathname + back.search + back.hash;
      pageParameters.set('return', curationReturn);
    }
  }
  const url = new URL(location.href);
  url.search = pageParameters.toString();
  history.replaceState(null, '', url.pathname + url.search + url.hash);
  document.querySelectorAll('.card').forEach(card => {
    delete card.dataset.predictionsLoaded;
  });
}
async function toggleCurationCompare(card, scene, button) {
  if (button.disabled) return;
  if (card._comparisonPanel) {
    card._comparisonPanel.hidden = !card._comparisonPanel.hidden;
    curationComparisonEnabled = !card._comparisonPanel.hidden;
    localStorage.setItem('tree-review:compare-enabled', curationComparisonEnabled ? '1' : '0');
    card.classList.toggle('comparing-models', !card._comparisonPanel.hidden);
    button.setAttribute('aria-pressed', String(!card._comparisonPanel.hidden));
    if(!curationComparisonEnabled)void loadPredictionOverlay(card,scene);
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
    const response = await fetch('/api/runs/chip/' + encodeURIComponent(scene.validation_chip_id)
      + '?' + new URLSearchParams({run: predictionRun, compare_run:pageParameters.get('compare_run') || 'auto'}));
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'Comparison unavailable');
    const available = payload.runs.filter(e => e.available);
    let current = available.find(e => e.run.run_id === payload.selected_run_id);
    if (!current) throw new Error('Current model has no predictions for this chip');
    const choices = payload.choices;
    if (choices.length<2) throw new Error('No compatible comparison model');
    const panel = document.createElement('div'); panel.className = 'curation-compare-panel fullscreen-only';
    panel.style.cssText = 'flex-basis:100%;padding:8px;border:1px solid #496252;border-radius:7px';
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
    const initialOther = payload.runs.find(e => e.run.run_id !== payload.selected_run_id);
    if (initialOther) select.value = initialOther.run.run_id;
    const layerToggle = (label, color) => {
      const wrap = document.createElement('label'); wrap.style.cssText = 'margin-right:12px;color:' + color;
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
      if (!other || !current) { note.textContent = 'No saved predictions for this chip in that run. Choose another model.'; return; }
      if (!['chip_pixels','resolution_m','output_stride'].every(k => current.display[k] === other.display[k])) {
        note.textContent = 'Image scales differ; comparison cannot be overlaid safely.'; return;
      }
      const threshold = Math.max(0, Math.min(1, Number(confidence.value)));
      if (!Number.isFinite(threshold)) { note.textContent = 'Enter confidence between 0 and 1.'; return; }
      for (const [entry, show, color, size] of [[other,showOther.checked,'rgba(183,154,255,0.65)',18],[current,showCurrent.checked,'#ffae39',12]]) {
        if (!show) continue;
        const scale = 100 * entry.display.output_stride / entry.display.chip_pixels;
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
          wrap.append(ring);
        }
      }
      const count = entry => entry.data.predictions.filter(p => p.score >= threshold).length;
      note.textContent = `Orange: ${count(current)} (${current.run.training_run_id || current.run.run_id}) · Purple: ${count(other)} · Thin circles = predicted crowns where available; small rings = centers. Colors identify models, not correctness. Overlays do not intercept curation clicks.`;
      if(badge){badge.textContent=`Comparison: ${count(current)} current · ${count(other)} other`;badge.title=note.textContent;}
    };
    let loadSequence = 0;
    const loadPair = async () => {
      const sequence = ++loadSequence, id = select.value;
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
    select.addEventListener('change', loadPair);
    currentSelect.addEventListener('change', loadPair);
    for (const input of [showCurrent,showOther,confidence]) input.addEventListener('change', render);
    card._comparisonPanel = panel; button.parentElement.append(panel);
    card.classList.add('comparing-models'); button.setAttribute('aria-pressed','true'); render();
  } catch (error) {
    button.title = error.message; button.textContent = 'Retry comparison';
    card.classList.remove('comparing-models');
    // Only fetch the single-model overlay if the comparison could not be loaded.
    await loadPredictionOverlay(card,scene);
    return;
  } finally { button.disabled = false; }
  button.textContent = 'Compare models';
}
"""
