# Champignons Radar

Indice météo de **pousse** (jamais de comestibilité) autour de Montpellier. Voir `PLAN.md` pour le plan, le statut et les limites.

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m radar check-sources   # Open-Meteo répond ?
.venv\Scripts\python -m radar v0              # météo + score + web/index.html
.venv\Scripts\python -m pytest                # tests (sans réseau)
```

Tous les seuils sont dans `config/config.yaml` et sont des hypothèses non calibrées.
La carte charge Leaflet et les tuiles OpenStreetMap en ligne.
