const BACKEND = 'https://luxe-radar.onrender.com';
const statusNode = document.querySelector('#engine-status');
const form = document.querySelector('#instant-search');
const queryInput = document.querySelector('#query');
let engineReady = false;

function setReady() {
  engineReady = true;
  statusNode.classList.add('ready');
  statusNode.innerHTML = '<span></span> Moteur prêt — recherche immédiate';
}

async function wakeEngine() {
  try {
    await fetch(`${BACKEND}/api/health`, { mode: 'no-cors', cache: 'no-store' });
    setReady();
  } catch (_) {
    statusNode.innerHTML = '<span></span> Le moteur sera relancé à ta recherche';
  }
}

function openSearch(query) {
  // /search exécute réellement la recherche côté Flask. La racine avec ?q=
  // ne ferait que préremplir le champ, ce qui obligeait à cliquer une seconde fois.
  const url = new URL('/search', BACKEND);
  url.searchParams.set('q', query);
  window.location.assign(url.toString());
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (!query) return;
  if (engineReady) return openSearch(query);
  const button = form.querySelector('button');
  button.disabled = true;
  button.textContent = 'Moteur en préparation…';
  statusNode.innerHTML = '<span></span> Réveil du moteur gratuit — reste sur cette page';
  await wakeEngine();
  openSearch(query);
});

document.querySelectorAll('[data-query]').forEach((button) => {
  button.addEventListener('click', () => {
    queryInput.value = button.dataset.query;
    queryInput.focus();
  });
});

wakeEngine();
