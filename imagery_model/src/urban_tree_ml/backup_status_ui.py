"""Visibility-aware backup polling, cached across full-page chip navigation."""

BACKUP_STATUS_JS = r"""
function backupStatusPoller(render, onError) {
  let pending=null,timer=null,stopped=false,nextDelay=60000;
  async function read(city,force) {
    const key='studio-backup-status:v1:'+city;
    if(!force)try {
      const cached=JSON.parse(sessionStorage.getItem(key)||'null');
      if(cached && Date.now()>=cached.at && Date.now()-cached.at<cached.ttl) {
        nextDelay=Math.min(nextDelay,cached.ttl-(Date.now()-cached.at));
        return cached.data;
      }
    }catch{}
    const response=await fetch('/api/backup-status?'+new URLSearchParams({city}),
      {signal:AbortSignal.timeout(15000)});
    if(!response.ok)throw Error('Backup status unavailable');
    const data=await response.json(),ttl=data.job?.running?5000:60000;
    nextDelay=Math.min(nextDelay,ttl);
    try{sessionStorage.setItem(key,JSON.stringify({at:Date.now(),ttl,data}));}catch{}
    return data;
  }
  function schedule(delay) {
    clearTimeout(timer);
    if(!stopped&&!document.hidden)timer=setTimeout(()=>refresh(),Math.max(250,delay));
  }
  function refresh(force=false) {
    if(pending)return force?pending.then(()=>refresh(true)):pending;
    if(stopped||(!force&&document.hidden))return Promise.resolve();
    clearTimeout(timer);nextDelay=60000;
    pending=Promise.resolve().then(()=>render(city=>read(city,force)))
      .catch(onError).finally(()=>{pending=null;schedule(nextDelay);});
    return pending;
  }
  document.addEventListener('visibilitychange',()=>{
    if(document.hidden)clearTimeout(timer);else schedule(250);
  });
  window.addEventListener('pagehide',()=>{stopped=true;clearTimeout(timer);});
  window.addEventListener('pageshow',()=>{stopped=false;schedule(1500);});
  // Let the new tile and its editable state load before ancillary status work.
  schedule(1500);
  return refresh;
}
"""
