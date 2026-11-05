# Champignons Radar

Indice météo de **pousse** (jamais de comestibilité) autour de Montpellier. Voir `PLAN.md` pour le plan, le statut et les limites.

**Le plus simple (Windows)** : double-cliquer sur `Radar.bat` (installe tout au premier lancement, puis met à jour la carte et l'ouvre) ; `Journal.bat` ouvre la saisie des sorties (données gardées sur le PC, `data/journal.db`).

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m radar check-sources   # Open-Meteo répond ?
.venv\Scripts\python -m radar fetch           # sources de terrain (~300 Mo, intégrité contrôlée)
.venv\Scripts\python -m radar terrain         # grille terrain 250 m -> data/processed
.venv\Scripts\python -m radar access          # temps de trajet (OSRM) + zones protégées, une fois
.venv\Scripts\python -m radar score           # météo + habitat -> score final J-7..J+7, carte web/index.html
.venv\Scripts\python -m radar report [--send] # créneaux + meilleures zones : texte, carte, PDF, email
.venv\Scripts\python -m radar v0              # météo seule (points 5 km) -> web/v0.html
.venv\Scripts\python -m pytest                # tests (sans réseau)
```

Email : copier `.env.example` en `.env` (local) ou créer les mêmes secrets GitHub pour l'envoi quotidien
automatique (`.github/workflows/daily.yml`). Rien de personnel dans le dépôt (public).

Tous les seuils (`config/config.yaml`) et poids d'habitat (`config/species.yaml`) sont des hypothèses non calibrées.
La carte charge Leaflet et les fonds IGN / OpenTopoMap en ligne.

Documentation : [méthode de calcul de l'indicateur](docs/METHODE.md) et [pistes d'amélioration](docs/AMELIORATIONS.md),
aussi en PDF dans `docs/pdf/` (régénérer : `python tools/md2pdf.py docs/METHODE.md docs/AMELIORATIONS.md`).
