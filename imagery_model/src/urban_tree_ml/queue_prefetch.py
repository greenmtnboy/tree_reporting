"""One queue member ahead, across full-page navigation; no editable-state cache."""

QUEUE_PREFETCH_JS = r"""
const queuePrefetchSlot = 'tree-review:next-chip:v1';
const queuePrefetchLimit = 2 * 1024 * 1024; // UTF-16 storage bytes, not whole model bundles.
const queuePrefetchTTL = 5 * 60 * 1000;
function assetKey(value) {
  const url = new URL(value, document.baseURI);
  if (url.origin !== location.origin) return '';
  url.searchParams.sort();
  return url.pathname + url.search;
}
function readQueueAssets() {
  if (pageParameters.has('acquisition')) return null;
  try {
    const raw = sessionStorage.getItem(queuePrefetchSlot);
    sessionStorage.removeItem(queuePrefetchSlot); // consume once; only the next member is retained
    if (!raw || raw.length * 2 > queuePrefetchLimit) return null;
    const value = JSON.parse(raw), p = pageParameters;
    if (value.expires < Date.now() || value.queue !== p.get('queue') ||
        value.chip !== p.get('queue_chip') || value.city !== p.get('city') ||
        value.run !== p.get('run') || value.preference !== savedComparison(value.run) || value.compare_run !== (p.get('compare_run') || 'auto')) return null;
    return value;
  } catch { return null; }
}
const enteredQueueAssets = readQueueAssets();
function prefetchedImage(value, kind = 0) {
  const fallback = enteredQueueAssets?.scene === null ? enteredQueueAssets?.image_urls?.[kind] : null;
  const cached = enteredQueueAssets?.images?.[assetKey(value)] || (fallback && enteredQueueAssets.images?.[assetKey(fallback)]);
  return enteredQueueAssets?.expires >= Date.now() && typeof cached === 'string' && cached.startsWith('data:image/png;base64,') ? cached : value;
}
async function fetchCurationAsset(value, options) {
  // Deliberately NOT a global fetch interceptor: /api/review-scene is always fresh.
  const cached = enteredQueueAssets?.json?.[assetKey(value)];
  if (cached && enteredQueueAssets.expires >= Date.now()) {
    return new Response(JSON.stringify(cached), {headers: {'Content-Type': 'application/json'}});
  }
  return fetch(value, options);
}
function queueTarget(context, index) {
  if (!context || index < 0 || index >= context.queue.chips.length) return null;
  const {queue} = context, chip = queue.chips[index];
  if (queue.split === 'registration') return {chip, split:'registration', city:queue.cities?.[index] || queue.city};
  if (queue.split === 'all') {
    const route = queue.routes?.[index];
    const entry = [3,4].includes(queue.version) ? {split:route[0] === 0 ? 'train':'validation', city:route[1],
      ...(queue.version===4?{acquisition:route[2],run:route[3],scene:route[4]||null}:{})} : queue.entries?.[chip];
    if (!entry || !['train','validation'].includes(entry.split)) return null;
    return {...entry,chip, split:entry.split, city:entry.city || queue.city || pageParameters.get('city')};
  }
  return {chip, split:queue.split === 'train' ? 'train':'validation', city:queue.city || pageParameters.get('city')};
}
function queueDestination(context, index, prepared = null) {
  const target = queueTarget(context,index);
  if (!target) return null;
  const direct = target.split === 'registration' || Boolean(prepared?.scene || target.scene);
  const url = new URL(direct ? '/registration' : target.split === 'train' ? '/curate-training':'/curate', location.origin);
  for (const key of ['run','compare_run','threshold','radius','acquisition']) {
    if (pageParameters.has(key)) url.searchParams.set(key,pageParameters.get(key));
  }
  if(context.queue.version===4&&context.queue.split==='all'){
    if(target.acquisition)url.searchParams.set('acquisition',target.acquisition);else url.searchParams.delete('acquisition');
    url.searchParams.set('run',target.run);url.searchParams.delete('compare_run');
  }
  url.searchParams.set('city',target.city);
  url.searchParams.set('queue',context.id);
  url.searchParams.set('return',curationReturn || '/model');
  if (direct) {
    url.searchParams.set('scene',prepared?.scene || target.scene || target.chip);
    if(target.scene&&!target.run)url.searchParams.set('source_predictions','unavailable');
    url.searchParams.set('fullscreen','1');
    url.searchParams.set('queue_chip',target.chip);
    if (prepared) {
      url.searchParams.set('run',prepared.run);
      if(prepared.inference_unavailable)url.searchParams.set('source_predictions','unavailable');
      url.searchParams.set('compare_run',prepared.compare_run);
    }
  } else url.searchParams.set('chip',target.chip);
  return url.pathname + url.search;
}
let queuePrefetchTimer = null, queuePrefetchController = null, preparedQueueMember = null, queuePrefetchIdentity = '';
function queuePrefetchSignature(context, target) {
  return JSON.stringify([context.id,target, predictionRun, pageParameters.get('compare_run') || 'auto',savedComparison(predictionRun),curationComparisonEnabled]);
}
function preparedNext(context, index) {
  const target = queueTarget(context,index), prepared = preparedQueueMember;
  return target && prepared && prepared.expires >= Date.now() &&
    prepared.identity === queuePrefetchSignature(context,target) ? prepared.member : null;
}
function scheduleQueuePrefetch() {
  if (pageParameters.has('acquisition')) return;
  clearTimeout(queuePrefetchTimer);
  const context = validationQueueContext(), target = queueTarget(context, (context?.index ?? -1)+1);
  if(context?.queue.split==='all'&&context.queue.version===4)return;
  // Never speculate from a gallery, outside a queue, or past its last member.
  if (!metadata.editor_scene_id || !target || target.split === 'registration' || !predictionRun ||
      pageParameters.get('source_predictions') === 'unavailable') return;
  const identity = queuePrefetchSignature(context,target);
  if (identity === queuePrefetchIdentity) return;
  queuePrefetchController?.abort();
  preparedQueueMember = null;
  queuePrefetchTimer = setTimeout(async () => {
    if (document.hidden) return;
    if (!serverRevision || savedVersion !== editVersion ||
        document.querySelector('.compare-models:disabled,[data-predictions-loaded="loading"]')) {
      scheduleQueuePrefetch(); return;
    }
    queuePrefetchIdentity = identity;
    const controller = new AbortController(); queuePrefetchController = controller;
    const deadline = setTimeout(() => controller.abort(), 20000);
    const options = {signal:controller.signal, priority:'low'};
    try {
      sessionStorage.removeItem(queuePrefetchSlot);
      const response = await fetch('/api/review-next?' + new URLSearchParams({
        city:target.city,chip:target.chip,split:target.split,run:predictionRun,
        compare_run:pageParameters.get('compare_run') || 'auto'}),options);
      if (!response.ok) return;
      const member = await response.json();
      const current = validationQueueContext();
      if (!current || identity !== queuePrefetchSignature(current,queueTarget(current,current.index+1))) return;
      const value = {...member, preference:savedComparison(member.run), queue:context.id, expires:Date.now()+queuePrefetchTTL, image_urls:member.images,images:{},json:{}};
      preparedQueueMember = {member,identity,expires:value.expires};
      function retain() {
        if (controller.signal.aborted) return;
        const encoded = JSON.stringify(value);
        if (encoded.length*2 <= queuePrefetchLimit) sessionStorage.setItem(queuePrefetchSlot,encoded);
      }
      retain();
      // Sequential, bounded requests: the foreground editor and saves take priority.
      for (const url of member.images) {
        const result = await fetch(url,options);
        if (!result.ok) continue;
        const blob = await result.blob();
        if (blob.size > queuePrefetchLimit/4 || blob.type !== 'image/png') continue;
        const data = await new Promise((resolve,reject) => {
          const reader = new FileReader(); reader.onload = () => resolve(reader.result);
          reader.onerror = reject; reader.readAsDataURL(blob);
        });
        value.images[assetKey(url)] = data; retain();
      }
      const url = curationComparisonEnabled
        ? '/api/runs/chip/'+encodeURIComponent(member.chip)+'?'+comparisonParameters(member.run,member.compare_run)
        : member.prediction_url;
      if (!url) return;
      const result = await fetch(url,options);
      if (result.ok) {
        const text = await result.text();
        if (text.length*2 <= queuePrefetchLimit) {value.json[assetKey(url)] = JSON.parse(text); retain();}
      }
    } catch { /* Speculation must never block navigation, saving, or ordinary loading. */ }
    finally { clearTimeout(deadline); }
  }, 600);
}
window.addEventListener('pagehide', () => {clearTimeout(queuePrefetchTimer);queuePrefetchController?.abort();});
document.addEventListener('visibilitychange', () => {if (!document.hidden) scheduleQueuePrefetch();});
"""
