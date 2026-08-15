(()=>{
  const select=document.querySelector('#risk-filter');
  const grid=document.querySelector('#results-grid');
  if(!select||!grid)return;
  const apply=()=>{
    const mode=select.value||'all';
    let visible=0;
    grid.querySelectorAll('.product-card').forEach(card=>{
      const risk=card.dataset.risk||'faible';
      const hidden=mode==='hide_high'?risk==='eleve':mode==='low_only'?risk!=='faible':false;
      card.hidden=hidden;
      if(!hidden)visible++;
    });
    const shown=document.querySelector('#shown-count');
    if(shown)shown.textContent=String(visible);
  };
  select.addEventListener('change',apply);
  document.querySelector('#apply-advanced')?.addEventListener('click',()=>setTimeout(apply,0));
  document.querySelector('#reset-advanced')?.addEventListener('click',()=>{select.value='all';setTimeout(apply,0)});
  new MutationObserver(apply).observe(grid,{childList:true});
  apply();
})();
