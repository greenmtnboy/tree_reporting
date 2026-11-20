// One panel per scene. Replacing its contents detaches obsolete requests from the UI.
function mountStreetExamples(card, panel, head, googleWrap, openGoogle) {
  const provider = document.createElement('select');
  provider.ariaLabel = 'Street imagery provider';
  provider.add(new Option('Google Street View', 'google'));
  provider.add(new Option('Mapillary', 'mapillary'));
  provider.value = 'google';
  head.prepend(provider);
  head.style.flexWrap = 'wrap';
  const body = document.createElement('section');
  body.className = 'street-example-body';
  panel.append(body);
  let target, version = 0, objectUrl;
  const city = document.querySelector('base').href.split('/city-assets/')[1].split('/')[0];

  function clear() {
    version++;
    card.classList.remove('mapillary-preview');
    card.querySelector('.street-view-camera').style.display = 'none';
    target = null;
    body.replaceChildren();
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
  }
  async function json(url, options) {
    const res = await fetch(url, options);
    const data = await res.json();
    if (!res.ok) throw Error(data.error || 'Street imagery request failed');
    return data;
  }
  function element(tag, text) {
    const node = document.createElement(tag);
    node.textContent = text;
    return node;
  }
  function showGoogle(message) {
    card.classList.remove('mapillary-preview');
    card.querySelector('.street-view-camera').style.display = 'none';
    card.querySelector('.street-view-location').textContent = message || 'Google Street View';
    body.replaceChildren();
    if (message) {
      const retry = element('button', 'Retry Mapillary');
      retry.onclick = () => refresh(target);
      body.append(element('p', message), retry);
    }
    googleWrap.style.display = streetViewEmbedApiKey ? '' : 'none';
    head.querySelector('.street-view-interaction').hidden = !streetViewEmbedApiKey;
    head.querySelector('.street-view-external').hidden = false;
    head.querySelector('.street-view-external').href = externalStreetViewUrl(target);
    if (streetViewEmbedApiKey) openGoogle(target);
  }
  async function refresh(sample) {
    clear();
    target = sample;
    if (provider.value === 'google') { showGoogle(''); return; }
    const generation = version;
    googleWrap.style.display = 'none';
    const iframe = googleWrap.querySelector('iframe');
    iframe.src = 'about:blank';
    delete iframe.dataset.viewpoint;
    head.querySelector('.street-view-interaction').hidden = true;
    head.querySelector('.street-view-external').hidden = true;
    card.classList.add('mapillary-preview');
    const location = head.querySelector('.street-view-location');
    location.textContent = 'Finding nearby frames…';
    try {
      const frames = await json('/api/street/nearby?' + new URLSearchParams({lat:sample.latitude, lon:sample.longitude}));
      if (generation !== version) return;
      if (!frames.length) throw Error('No nearby Mapillary frames facing this tree.');
      location.textContent = 'Choose a view and drag a box around the tree.';
      const chooser = document.createElement('select');
      chooser.ariaLabel = 'Mapillary frame';
      frames.forEach((f,i) => chooser.add(new Option('View ' + (i+1) + ' · ' + f.meters + ' m', f.id)));
      const editor = document.createElement('div');
      body.append(chooser, editor);
      let frameVersion = 0;
      async function loadFrame() {
        const frameGeneration = ++frameVersion, id = chooser.value;
        const current = () => generation === version && frameGeneration === frameVersion;
        card.querySelector('.street-view-camera').style.display = 'none';
        location.textContent = 'Loading Mapillary frame…';
        editor.replaceChildren(element('p','Loading frame…'));
        try {
          const response = await fetch('/api/street/photo?id=' + encodeURIComponent(id));
          if (!response.ok) throw Error((await response.json()).error);
          const blob = await response.blob();
          if (!current()) return;
          if (objectUrl) URL.revokeObjectURL(objectUrl);
          objectUrl = URL.createObjectURL(blob);
          const img = new Image();
          img.src = objectUrl;
          await img.decode();
          if (!current()) return;
          const frame = frames[chooser.selectedIndex];
          const camera = frame.camera;
          location.textContent = camera
            ? renderStreetViewCamera(card, sample, {provider:'Mapillary', location:camera, heading:camera.heading,
                is_pano:camera.is_pano}) + ' · ' + frame.meters + ' m from tree'
            : 'Mapillary camera position unavailable';
          const canvas = document.createElement('canvas');
          canvas.width = img.naturalWidth;
          canvas.height = img.naturalHeight;
          canvas.style.cssText = 'display:block;width:100%;touch-action:none;cursor:crosshair';
          canvas.ariaLabel = 'Drag to crop the tree';
          const ctx = canvas.getContext('2d');
          let box = null, start = null, revision = 0, busy = false;
          const input = document.createElement('input');
          input.placeholder = 'Scientific species name';
          input.ariaLabel = input.placeholder;
          input.value = sample.streetSpecies || '';
          let source = sample.streetSource;
          const suggest = element('button','Suggest species');
          const save = element('button','Save labeled crop');
          const status = element('p','');
          status.setAttribute('role','status');
          const options = element('div','');
          function controls() {
            suggest.disabled = busy || !box;
            save.disabled = busy || !box || input.value.trim().split(/\s+/).length < 2;
          }
          function redraw() {
            ctx.drawImage(img,0,0);
            if (box) {
              ctx.strokeStyle = '#00ff9d';
              ctx.lineWidth = 3*canvas.width/Math.max(1,canvas.clientWidth);
              ctx.strokeRect(box[0],box[1],box[2]-box[0],box[3]-box[1]);
            }
            controls();
          }
          function point(e) {
            const r = canvas.getBoundingClientRect();
            return [Math.round(Math.max(0,Math.min(1,(e.clientX-r.left)/r.width))*canvas.width),
                    Math.round(Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))*canvas.height)];
          }
          canvas.onpointerdown = e => {
            if (busy || e.button !== 0) return;
            start = point(e); box = null; revision++;
            if (source === 'plantnet') {input.value = ''; source = 'curator';}
            options.replaceChildren(); status.textContent = '';
            canvas.setPointerCapture(e.pointerId); redraw();
          };
          function move(e) {
            if (!start) return;
            const [x,y] = point(e);
            box = [Math.min(start[0],x),Math.min(start[1],y),Math.max(start[0],x),Math.max(start[1],y)];
            if (box[2]-box[0]<2 || box[3]-box[1]<2) box = null;
            redraw();
          }
          canvas.onpointermove = move;
          canvas.onpointerup = e => { move(e); start = null; };
          canvas.onpointercancel = () => { start = null; box = null; redraw(); };
          input.oninput = () => { source = 'curator'; controls(); };
          async function submit(action) {
            if (busy || !box) return;
            const original = revision;
            const payload = {scene:card.dataset.scene, tree:sample.streetTree, latitude:sample.latitude,
              longitude:sample.longitude, image_id:id, crop:box, species:input.value.trim(), label_source:source};
            busy = true; controls(); status.textContent = action === 'save' ? 'Saving…' : 'Identifying crop…';
            try {
              const result = await json('/api/street/' + action + '?city=' + encodeURIComponent(city), {
                method:'POST', headers:{'Content-Type':'application/json','X-Studio-Species':'1'},body:JSON.stringify(payload)});
              if (!current() || original !== revision) return;
              if (action === 'save') {status.textContent = 'Saved ' + result.species; return;}
              options.replaceChildren();
              status.textContent = result.length ? 'Choose a suggestion or enter your own label.' : 'No species suggestion.';
              for (const r of result) {
                const choice = element('button',r.species + ' · ' + Math.round(100*r.score) + '%');
                choice.onclick = () => {input.value = r.species; source = 'plantnet'; controls();};
                options.append(choice);
              }
            } catch (e) { if (current()) status.textContent = e.message; }
            finally { busy = false; controls(); }
          }
          suggest.onclick = () => submit('identify');
          save.onclick = () => submit('save');
          const credit = element('a','Mapillary · ' + (decodeURIComponent(response.headers.get('X-Creator') || '') || 'contributor') + ' · CC BY-SA 4.0');
          credit.href = 'https://www.mapillary.com/app/?pKey=' + id;
          credit.target = '_blank'; credit.rel = 'noopener';
          const targetLabel = element('p', 'Drag a box around the selected tree · ' + sample.streetTree);
          editor.replaceChildren(canvas,credit,targetLabel,input,suggest,save,options,status);
          redraw();
        } catch(e) {
          if (!current()) return;
          if (chooser.selectedIndex === 0 && frames.length > 1) {
            chooser.selectedIndex = 1;
            await loadFrame();
          } else {
            showGoogle(e.message + ' · Showing Google Street View.');
          }
        }
      }
      chooser.onchange = loadFrame;
      await loadFrame();
    } catch (e) {
      if (generation === version) showGoogle(e.message + ' Showing Google Street View.');
    }
  }
  provider.onchange = () => {
    if (target) {
      card.streetViewGeneration++;
      refresh(target);
    }
  };
  body.addEventListener('keydown', e => e.stopPropagation());
  return {refresh, clear};
}
