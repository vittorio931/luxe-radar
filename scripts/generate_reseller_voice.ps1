$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$media = Join-Path $project 'media'
New-Item -ItemType Directory -Path $media -Force | Out-Null
$voice = New-Object -ComObject SAPI.SpVoice
$voice.Voice = $voice.GetVoices() | Where-Object { $_.GetDescription() -like '*Hortense*' } | Select-Object -First 1
$voice.Rate = -1
$voice.Volume = 100
$stream = New-Object -ComObject SAPI.SpFileStream
$target = Join-Path $media 'luxe_radar_reseller_voix.wav'
$stream.Open($target, 3, $false)
$voice.AudioOutputStream = $stream
$text = @"
Quand on fait de la revente, le plus long, ce n'est pas toujours de vendre. C'est de trouver la bonne pièce, au bon prix, avant les autres.

LUXE RADAR réunit les annonces de Vinted, eBay, Grailed et soixante-sept behaviour dans une seule recherche. Tu indiques un article, ton budget, et le Radar classe les résultats les plus pertinents en premier, puis charge la suite automatiquement.

Tu peux mettre les meilleures annonces en favoris, créer des alertes, comparer plusieurs opportunités et suivre les variations de prix.

Dans le Studio, calcule ta marge après les frais, vérifie ton budget et utilise la checklist avant achat. Dans le Portfolio, enregistre ton stock, ton prix d'achat, ton prix de vente et ton bénéfice réel.

Moins de temps à ouvrir dix onglets. Plus de temps pour analyser, négocier et vendre.

Commence gratuitement avec LUXE RADAR. Trouve avant les autres.
"@
$voice.Speak($text) | Out-Null
$stream.Close()
Write-Output $target
