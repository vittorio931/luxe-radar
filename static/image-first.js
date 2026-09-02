(() => {
const boot=window.LUXE_RADAR||{},input=document.querySelector('#image-search-file'),button=document.querySelector('#image-search-btn');
const read=file=>new Promise((ok,fail)=>{const reader=new FileReader();reader.onload=()=>ok(reader.result);reader.onerror=fail;reader.readAsDataURL(file)});
const asFile=url=>{const [meta,data]=url.split(','),mime=meta.match(/data:([^;]+)/)?.[1]||'image/jpeg',raw=atob(data),bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);return new File([bytes],'recherche-image',{type:mime})};
button?.addEventListener('click',async()=>{
  const file=input?.files?.[0];if(boot.token||!file)return;
  button.disabled=true;button.textContent='Identification…';const body=new FormData();body.append('image',file);
  try{
    const response=await fetch('/api/image-query',{method:'POST',body,headers:{'X-CSRF-Token':boot.csrfToken||''}}),data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.error||'Identification impossible');
    document.querySelector('#marque').value=data.query;sessionStorage.setItem('lr.pendingVisual',await read(file));document.querySelector('#search-form').requestSubmit();
  }catch(error){window.alert(error.message)}finally{button.disabled=false;button.textContent='Identifier et rechercher'}
});
if(boot.token){const pending=sessionStorage.getItem('lr.pendingVisual');if(pending){sessionStorage.removeItem('lr.pendingVisual');setTimeout(()=>window.LuxeRadarRankImage?.(asFile(pending)),250)}}
})();
