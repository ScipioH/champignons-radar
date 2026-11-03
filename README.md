# Champignons Radar

Indice météo de **pousse** (jamais de comestibilité) autour de Montpellier. Voir `PLAN.md` pour le plan, le statut et les limites.

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m radar check-sources   # Open-Meteo répond ?
.venv\Scripts\python -m radar fetch           # sources de terrain (~300 Mo, intégrité contrôlée)
.venv\Scripts\python -m radar terrain         # grille terrain 250 m -> data/processed
.venv\Scripts\python -m radar score           # météo + habitat -> score final, carte web/index.html
.venv\Scripts\python -m radar v0              # météo seule (points 5 km) -> web/v0.html
.venv\Scripts\python -m pytest                # tests (sans réseau)
```

Tous les seuils (`config/config.yaml`) et poids d'habitat (`config/species.yaml`) sont des hypothèses non calibrées.
La carte charge Leaflet et les tuiles OpenStreetMap en ligne.
