# Champignons Radar — PLAN (v2, révisé)

Outil local/gratuit qui croise météo, relief, essences forestières et sol pour estimer, dans le temps et l'espace, les meilleures opportunités de **pousse** à ≤ 1 h de Montpellier, et qui envoie un rapport automatique.

> ⚠️ Le score est un **indicateur de probabilité de pousse**, jamais une validation de comestibilité.
> Tous les seuils sont des **hypothèses non calibrées** tant que le journal de sorties n'a pas ≥ 20 entrées.

Origine : plan initial téléchargé le 2026-11-02 (`champignons-radar-plan.md`), révisé ci-dessous après relecture critique.

---

## 0. Prompt de reprise (à coller dans Claude Code, dans ce dossier)

```
Tu reprends le projet "champignons-radar". Lis PLAN.md (section 8 = statut) et reprends la
prochaine étape non cochée. Respecte les contraintes (§1) et la méthode (§2).
```

## 1. Contraintes

- 100 % local/gratuit, sans base serveur : Python, fichiers (JSON/GeoJSON/Parquet), SQLite (journal), GitHub Pages + Actions plus tard.
- Un seul point d'entrée : `python -m radar <commande>` (pas de `make` : absent sous Windows). Config unique dans `config/config.yaml`, aucune valeur en dur.
- Toute modification est décrite et justifiée (message de commit détaillé).
- Franchise : si une source ne marche pas ou si un score est peu fiable, le dire, ne pas contourner en silence.
- Aucune donnée personnelle dans le dépôt (tokens en secrets, journal de sorties gitignoré).

## 2. Méthode

- Phase par phase ; on ne passe à la suivante qu'après la « Définition de fini ».
- Avant tout téléchargement externe : vérifier URL + licence + date, consigner dans `data/raw/SOURCES.md`.
- Tests sur données simulées uniquement (pas de réseau dans les tests).
- Un test d'intégration réseau séparé (`python -m radar check-sources`) pour vérifier que les sources répondent.

## 3. Révisions par rapport au plan initial (réponses aux points faibles)

| # | Point faible | Décision |
|---|---|---|
| 1 | Score purement multiplicatif : un seul facteur à 0 (ex. une nuit de gel) écrase tout | **Moyenne géométrique pondérée avec plancher** par composante (`floor`), plus **gel = facteur doux** dépendant du nombre de nuits, de leur sévérité et de l'altitude (jamais 0 sec sauf gel sévère) |
| 2 | Seuils non sourcés, risque de fausse confiance | Chaque seuil est dans la config avec `status: hypothese` ; la carte et le rapport affichent « indice non calibré » tant que le journal < 20 sorties ; le score est présenté en **classes** (faible / moyen / bon / très bon), pas en pourcentage de chance |
| 3 | Maille V0 de 5 km trop grossière pour la fixture | V0 = classement **relatif** entre zones à 5 km (la météo varie peu à cette échelle) ; ne prétend pas à la précision locale. Précision locale = V1 (500 m, terrain). L'altitude renvoyée par l'API sert déjà au gel |
| 4 | Phase 2 trop chargée | Scindée en **2a terrain**, **2b habitat**, **2c trajet** |
| 5 | Sources/licences possiblement périmées | Commande `check-sources` + `SOURCES.md` obligatoire, aucune URL non testée en dur |
| 6 | Quotas Open-Meteo | Requêtes groupées (≤ 50 points/requête), cache disque par jour, pause entre lots, retry sur 429, limite d'appels journalière en config |
| 7 | Dépôt public (Pages) expose les zones | Sorties publiées = cellules agrégées (jamais le journal de sorties, jamais de points GPS personnels) ; dépôt privé par défaut, Pages seulement après décision explicite |
| 8 | « Hérault favorable » trop vague comme critère de fini | Critères **chiffrés sur séries simulées** reprenant la situation du 2/11/2026 (cf. §6) |
| 9 | Trajet : rayon à vol d'oiseau | V0 : rayon haversine 60 km (**approximation déclarée**) ; 2c : vrai temps de trajet (OpenRouteService/OSRM) |

## 4. Architecture

```
champignons-radar/
├─ PLAN.md  README.md  requirements.txt  .gitignore
├─ config/config.yaml        # centre, rayon, maille, seuils (status), poids, quotas
├─ config/species.yaml       # (phase 2b)
├─ data/{raw,cache,processed,out}/   # gitignorés sauf out/ publié
├─ radar/
│  ├─ config.py  grid.py  weather.py  score.py  mapgen.py  cli.py  __main__.py
│  └─ (plus tard) terrain.py travel.py report.py journal.py
├─ web/index.html            # carte générée (Leaflet), données embarquées
└─ tests/
```

Principe : terrain calculé une fois (figé) ; météo recalculée chaque jour ; la carte ne lit que des fichiers.

## 5. Modèle de score (V0 = météo seule)

Entrées par point : pluie journalière (30 j passés + 7 j prévus), Tmin/Tmoy, altitude.

Composantes (0–1) :
- `pluie` : cumul max sur 2 jours glissants dans les 15 derniers jours ; 0 sous 10 mm, 1 entre 20 et 60 mm, décroît (saturation) jusqu'à 0,5 vers 150 mm.
- `delai` : jours depuis la pluie déclenchante (≥ 20 mm sur 2 j) ; 0 avant 4 j, plateau 8–14 j, décroissance jusqu'à 0 vers 35 j.
- `temp` : moyenne des Tmin sur 7 j ; 1 si ≥ 8 °C, 0 à 3 °C.
- `secheresse` : pluie moyenne (mm/j) depuis la pluie déclenchante ; pénalité si trop sec.
- `prevision` : pluie prévue J+1..J+3 ; neutre si 5–40 mm, léger malus si très sec ou très forte pluie.

Score météo = moyenne géométrique pondérée des composantes (plancher `floor`) × facteur gel.
Facteur gel : `1 − pénalité_par_nuit × nuits_de_gel × (1,5 si altitude > 800 m)`, borné à ≥ `gel_floor` ; gel sévère (Tmin ≤ −4 °C) → facteur 0,1.

Score final (2b+) : `100 × meteo^a × habitat^b × saison × accessibilite` (a = 0,6, **b = 1,0** depuis le 2026-11-04 : avec 0,4, une cellule à 10 % d'habitat gardait 40 % du score ; accessibilité = 1 jusqu'en 2c). V0 : `100 × meteo`.
Habitat (`config/species.yaml`) = forêt × géologie × altitude × exposition, forêt et géologie = moyennes des poids pondérées par la part de chaque classe dans la cellule (approximation : pas de croisement pixel à pixel). Essence non précisée = prior intermédiaire ; hors couverture = inconnu (NaN), jamais 0. Météo ramenée aux cellules 250 m par IDW des points 5 km (lissage, pas d'information nouvelle).

Ce que le score ne fait pas : comestibilité, micro-habitat, pluie très locale. Il peut être faux : d'où le journal et la calibration.

## 6. Phases et « Définition de fini »

**Phase 1 — V0 : météo + carte statique**
Grille ~5 km dans 60 km autour de Montpellier ; Open-Meteo (historique 30 j + prévision 7 j, cache) ; score météo ; carte HTML Leaflet autonome ; tests.
*Fini quand* : `python -m radar v0` produit `web/index.html` ouvrable en local ; tests verts avec ces cas simulés :
 (a) situation 2/11/2026 (≈ 50 mm/15 j, pluie ≥ 20 mm il y a ~8 j, Tmin ~9 °C, 0 gel) → score ≥ 70 ;
 (b) 3 nuits de gel récentes → score < 60 % du cas (a) mais > 0 ;
 (c) 30 j sans pluie → score < 30 ;
 (d) 200 mm la veille → score < 40 (trop tôt) ;
 (e) monotonie : score décroît quand les jours de délai dépassent 20 j.

**Phase 2a — terrain** : scripts de téléchargement + `SOURCES.md` (BD Forêt, BRGM, MNT) ; prétraitement ; grille 500 m. *Fini* : fichier traité reproductible, sources documentées.
**Phase 2b — habitat** : `species.yaml`, score habitat par famille (cèpes, lactaires délicieux, girolles), score final. *Fini* (réécrit le 2026-11-04, l'ancien critère « châtaigneraies sur schiste à Monoblet/Saint-Hippolyte » étant démenti par la carte géologique) — vérifié automatiquement par `python -m radar score` :
 (a) les 5 % de cellules les mieux notées « cèpes » sont majoritairement sur sol acide ET châtaignier/chênes/hêtre ;
 (b) Gardiole (3 km) : habitat lactaires > cèpes ;
 (c) Montpellier centre (1,5 km) : habitat ≤ 0,05 pour toutes les familles ;
 (d) cellules hors couverture = « inconnu », aucune cellule couverte sans valeur ;
 (e) avis de l'utilisateur sur la carte (coins connus).
 ⚠️ (a)–(d) vérifient que le code applique les hypothèses de façon cohérente sur le vrai terrain, **pas** que les hypothèses sont justes.
**Phase 2c — trajet** : temps de trajet réel ≤ 60 min, masque zones interdites. *Fini* : aucune cellule hors périmètre.
**Phase 3 — V2 : temporel + automatisation + rapports** : curseur J-7→J+7, rapport (créneaux week-ends/fériés, top 3 zones, vigilance, chasse), **email + PDF** (cf. §7), `daily.yml`.
**Phase 4 — V3 : journal et calibration** : SQLite `sorties`, ≥ 20 sorties avant recalibration, rapport d'évaluation.

## 7. Décisions par défaut (modifiables)

- Maille : **terrain/habitat 250 m, définitif** (décision du 2026-11-02 ; pas de 100 m). Météo : voir §7bis (downscaling).
- Rapports : **email** (décision du 2026-11-02, remplace Telegram) : corps de mail = rapport, image de la carte zoomée sur la zone utile en pièce jointe/inline, et le tout repris en **PDF** joint. L'adresse destinataire et les identifiants SMTP vont **uniquement dans les secrets GitHub / `.env` local** (dépôt public : jamais dans le code ni la config). Choix du fournisseur SMTP d'envoi à vérifier en phase 3 (l'authentification SMTP de Microsoft/Outlook personnel a changé ces dernières années ; Gmail avec mot de passe d'application est le repli probable). Rendu de l'image côté serveur (Leaflet ne rend pas en headless) : matplotlib + fond de carte, à trancher en phase 3 en respectant la politique d'usage des tuiles ; PDF via une lib pure Python (reportlab).
- Disponibilité : week-ends + jours fériés + **soirées de semaine tant que la lumière le permet** (départ 17 h ; il faut qu'il reste ≥ `min_forage_minutes` de jour à l'arrivée). Calcul de l'heure de coucher du soleil en phase 3. Test d'ordre de grandeur : à Montpellier, avec trajet 60 min + 60 min de cueillette, il faut un coucher ≳ 19 h → possible jusqu'à ~fin septembre, impossible dès le changement d'heure.
- Familles de test : cèpes, puis lactaires, chanterelles.
- Dépôt GitHub **public** sur le compte perso (décision explicite, 2026-11-02). Conséquence assumée : zones et scores visibles. Règle maintenue : aucun secret, aucun journal de sorties, aucune coordonnée personnelle dans le dépôt.

## 7bis. Météo fine : downscaling (décision du 2026-11-02)

Principe : on distingue ce qui apporte de l'information réelle de ce qui ne fait que lisser.

| Variable | Méthode | Fiabilité |
|---|---|---|
| Interpoler les mailles de 5 km vers 250 m | Interpolation bilinéaire/IDW | **Aucune information nouvelle** : lissage seulement. Acceptable pour la pluie à défaut de mieux, à ne pas présenter comme de la précision |
| Température | Correction d'altitude : `T_cellule = T_point + gradient × (alt_cellule − alt_point)`, gradient ≈ −6,5 °C/km (config) | Bonne en journée. **Mauvaise pour Tmin par nuit claire** (inversions, cuvettes froides : le gel de fond de vallon est sous-estimé). Limite à documenter dans le rapport |
| Pluie | Pas de correction orographique par défaut. Option : gradient pluie/altitude **ajusté chaque jour sur les données elles-mêmes** (régression locale), désactivé par défaut tant qu'il n'est pas validé | Faible : la pluie cévenole est très locale. À valider, jamais par défaut |
| Modèle plus fin | **AROME France (Météo-France, 1,5 km) via Open-Meteo** (`models=meteofrance_arome_france`). Testé le 2026-11-02 : répond avec 30 j d'historique, sans valeur manquante, relief différencié à 2,5 km | Vraie information supplémentaire, préférable à l'interpolation. À étudier en 2a : grille ~2 km = ~2800 points (quota/jour à relever en connaissance de cause), emprise limitée à la France, historique plus court à confirmer |

Plan : en 2a, comparer sur 1–2 jours la grille 5 km (best_match) et AROME ~2 km ; garder AROME seulement si le gain est visible et le quota tenable ; appliquer la correction d'altitude à la température dans tous les cas. Aucune de ces corrections n'est validée sans journal de sorties.

### Résultats du test comparatif (2026-11-02) — `python -m radar compare-models`
Zone : 20 km autour de Saint-Hippolyte-du-Fort, maille 2 km, 309 points. Fenêtre = 15 derniers jours.

| | best_match | AROME HD (1,5 km) | AROME seamless |
|---|---|---|---|
| Pluie 15 j moyenne (mm) | 161 | 227 | 179 |
| Valeurs distinctes de pluie (/309) | 214 | 264 | 260 |
| Écart-type du score | 7,6 | 5,1 | 4,7 |
| Prévision J+1..J+3 | complète | **manquante dès J+2** (618 valeurs absentes) | complète |
| Corrélation pluie↔altitude | +0,50 | −0,22 | −0,45 |
| Gradient Tmin (°C/km) | −1,1 | −1,6 | −1,1 |

Corrélation de la pluie 15 j entre modèles : best_match↔HD 0,24 ; best_match↔seamless −0,19 ; HD↔seamless 0,62. Classe de score différente pour ~25 % des points entre best_match et AROME.

**Ce que ça dit, honnêtement :**
1. Les modèles **ne s'accordent pas** sur la pluie (écart moyen 48–74 mm sur 15 j, corrélations faibles voire négatives). Sans observation, on ne peut **pas** dire lequel a raison : la différence de résolution n'est pas une preuve de justesse. Ces « jours passés » sont des analyses/prévisions courtes enchaînées, pas des mesures.
2. **AROME HD seul est inutilisable** pour le score (prévision absente dès J+2) ; `meteofrance_seamless` complète avec ARPEGE.
3. AROME donne plus de détail spatial (plus de valeurs distinctes) mais **réduit** l'écart-type du score : le gain de discrimination n'est pas démontré.
4. **Correction au plan** : le gradient de Tmin mesuré est **−1,1 à −1,6 °C/km**, très loin de −6,5 (inversions nocturnes de novembre, confirmant la réserve sur le gel). Le gradient fixe de −6,5 °C/km est **abandonné pour Tmin** ; on utilisera un gradient empirique ajusté sur les données du jour, ou rien tant que non validé.
5. Signe opposé de la corrélation pluie↔altitude entre best_match (+) et AROME (−) : sur un seul épisode, on ne peut pas conclure.

**Décision :** aucun changement de modèle en production tant que non validé sur observations. Prochaine étape = validation sur stations Météo-France (ci-dessous).

### Source d'observations vérifiée (2026-11-02)
Données climatologiques de base – quotidiennes (Météo-France, data.gouv.fr, dataset `6569b51ae64326786e4e8e1a`), **Licence Ouverte 2.0**, mise à jour quotidienne. Fichiers utiles (≈0,5 Mo chacun) : `.../BASE/QUOT/Q_34_latest-2025-2026_RR-T-Vent.csv.gz` et `Q_30_...` sous `https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/`. Contiennent pluie (RR) et températures (TN/TX) par station, jusqu'à ~J-3.
Plan de validation : pour chaque station de la zone avec RR/TN renseignés, comparer sur les 30 derniers jours les valeurs du modèle (best_match, AROME HD, seamless) à la mesure : biais, MAE, corrélation de la pluie 15 j, et taux de bonne détection de la pluie déclenchante (≥ 20 mm / 2 j). Critère de décision : garder un modèle si son MAE de pluie 15 j est inférieur d'au moins ~20 % à celui de best_match sur ≥ 10 stations ; sinon rester sur best_match.

### Licences / attribution
- Open-Meteo gratuit : usage **non commercial**, attribution **CC BY 4.0** obligatoire (ajoutée à la carte ; à reprendre dans le rapport email/PDF). Quota gratuit : 600/min, 5 000/h, 10 000/jour ; les requêtes > 2 semaines comptent comme plusieurs appels (comptage exact par nombre de points non documenté : garde-fou conservateur conservé).
- Météo-France données publiques : Licence Ouverte 2.0 (mention de la source requise).

## 8. Statut

- [x] Plan révisé
- [x] Phase 1 — V0 : 20 tests verts (critères a–e), run réel du 2026-11-02 OK (437 points, 281 terrestres scorés), carte `web/index.html`
- [x] Phase 2a — terrain : sources vérifiées/téléchargées/auditées (`SOURCES.md`) ; prétraitement `python -m radar terrain` (19 s) → 124 172 cellules de 250 m (`data/processed/terrain_250m.npz` + `terrain_meta.json` + `terrain_report.txt`) ; 61 tests verts dont une chaîne complète sur fichiers synthétiques
- [x] Phase 2b — habitat : `config/species.yaml` (tableau validé le 2026-11-04, 3 familles, pas d'autres), `radar/habitat.py`, carte raster 250 m `web/index.html` (couches score + habitat par famille, clic = valeurs) ; critères (a)–(d) OK sur données réelles ; 78 tests verts. **(e) en attente : avis de l'utilisateur sur la carte.**
- [x] (avancé de la phase 3, 2026-11-04) Curseur temporel J-7 → J+7 sur la carte : score final par jour (saison/exposition du mois de chaque jour), météo 40 j passés + 11 j prévus (≈ 1 300 points-requêtes/jour sur le quota), avertissements rétrospectif / prévision lointaine. Clé de cache météo corrigée (elle n'incluait pas la fenêtre demandée). Carte ~3,2 Mo. Lanceur double-clic `Radar.bat`. Fonds de carte IGN (OSM et CARTO bloqués en `file://`).
- Extension France entière : **écartée pour l'instant** (2026-11-04, choix utilisateur) — estimée à ~10 Go de terrain, carte en tuiles, météo limitée à ~20 km par le quota Open-Meteo gratuit.
- [x] Phase 2c — trajet (2026-11-04) : `python -m radar access` (calcul unique, ~1 min) : OSRM voiture depuis le départ vers 2 202 points tous les 2 km + marche 4 km/h depuis la route, interpolé sur les cellules ; zones protégées PatriNat (Géoplateforme IGN, WFS). Cellules > 60 min retirées de la carte (critère « aucune cellule hors périmètre » vérifié : 0). Réserves naturelles nationales/régionales et arrêtés de biotope : score × 0 par prudence (le règlement de chaque site fait foi) ; cœur du Parc national des Cévennes : signalé « cueillette réglementée », non exclu. Couches « Temps de trajet » et « Zones protégées » sur la carte. Départ = centre de Montpellier, ou domicile via `RADAR_HOME_LAT/LON` (`.env` / secrets). Sorties générées (`web/index.html`, `data/out/`) **retirées du dépôt public** : le périmètre révélerait le domicile.
  **Constat** : depuis le centre de Montpellier, médiane 62 min, seulement 47 % des cellules à ≤ 60 min ; les meilleures zones à cèpes (Cévennes, Sumène 61 min) tombent juste au-delà → seuil à rediscuter (75 min ?). Limites : OSRM sans trafic (temps optimistes en heure de pointe), interpolation 2 km (une vallée voisine sans pont peut être sous-estimée).
- [x] Phase 3 — rapport (2026-11-04) : `python -m radar report [--send]` → `data/out/report/<date>/` (texte, email HTML avec carte intégrée, carte PNG fond Plan IGN, PDF reportlab, `.eml`). Créneaux J..J+7 : week-ends + fériés (calcul de Pâques), soirs de semaine seulement si (coucher du soleil − 17 h − trajet) ≥ 60 min (coucher NOAA, vérifié sur les solstices parisiens). Top 3 zones par famille et par jour, séparées de ≥ 5 km, avec forêt/sol/altitude/trajet/lien carte. Rappels : comestibilité, non calibré, vigilance Météo-France (lien, pas d'API sans clé), chasse (sept.–févr.), zones réglementées, attributions. Workflow `.github/workflows/daily.yml` (6 h 30) : terrain en cache, trajets recalculés, journaux sans données personnelles. **En attente : secrets SMTP** (Gmail + mot de passe d'application recommandé, cf. `.env.example`).
- [x] Phase 4 — outillage du journal (2026-11-04) : `data/journal.db` (SQLite, **non versionné**), page de saisie locale `python -m radar journal` / `Journal.bat` (carte cliquable, 127.0.0.1 seulement, protections Host + en-tête X-Radar), commandes `journal ajouter|liste|evaluer|recalculer`. Chaque sortie stocke l'instantané du modèle pour CE jour et CE lieu (météo Open-Meteo prévision ou archive selon l'ancienneté, habitat de la cellule, score, empreinte des réglages) ; `recalculer` met à jour après un changement de réglages. Évaluation : taux de réussite par classe prédite + corrélation de rang score↔résultat ; « non calibré » sous 20 sorties.
- [x] Documentation (2026-11-05) : `docs/METHODE.md` (méthode de calcul), `docs/AMELIORATIONS.md` (pistes), PDF dans `docs/pdf/` (`tools/md2pdf.py`).
- [x] Améliorations (2026-11-07), détail dans `docs/AMELIORATIONS.md` :
  - assèchement par bilan d'eau du sol (pluie − 0,7 × ET0, réserve 40 mm), repli sur l'ancienne mesure sans ET0 ;
  - `python -m radar controle-pluie` : 44 stations Météo-France (34 + 30), 90 jours, prévisions archivées Open-Meteo, mêmes jours pour tous les modèles. best_match : cumul 0,66 de l'observé, 64 % des pluies déclenchantes vues, erreur score 12 pts ; meteofrance_seamless : 0,98, 81 %, 6,7 pts → **modèle principal = meteofrance_seamless**, complété par best_match au-delà de J+3 (carte, rapport et journal) ;
  - trajet maximal 75 min (au lieu de 60) ;
  - repli hors ligne : si Open-Meteo est injoignable, dernière météo en cache + avertissement sur la carte (constaté : le lancement local échouait sur une panne passagère ; la CI, elle, passait).
  - Constat : une nouvelle pluie déclenchante remet le délai à zéro (chute du score les 4-8/11 malgré la poussée en cours) → piste A8.
- [x] A8, enchaînement des pluies (2026-11-08, validé par l'utilisateur) : pluie < 50 mm/2 j pendant une poussée active → prolongation (montée depuis le début, déclin depuis la dernière pluie) ; pluie ≥ 50 mm → nouvelle poussée, l'ancienne « abîmée » garde 0,5 × son score météo si c'est mieux. Constat : à 0,5, effet proche du plancher existant (≈ 40 vs 42 les 4-8/11) ; la prolongation, elle, ajoute des zones « bon » les 9-11/11.
- [x] Accès à pied et foncier (2026-11-11, demandé par l'utilisateur après une sortie en versants raides et
  terrains privés) :
  - `python -m radar pied` : pente 50 m (Copernicus) + distance aux voies BD TOPO (WFS, ~545 000 tronçons). Difficulté = rampe 15° → 30°, × 0,5 au bord d'une voie, 1 au-delà de 1 km ; score × (1 − 0,5 × difficulté). Constat : la 1re règle (« raide ET > 100 m d'une voie ») ne pénalisait presque rien, le réseau IGN étant très dense.
  - `python -m radar foncier` : cadastre Etalab (parcelles, bâtiments) + DGFiP personnes morales. Abords bâtis (< 30 m d'un bâtiment) × 0, public (groupes 1-2-3-4-9 ou forme 7xxx) × 1, non cadastré × 1, privé × `private_factor`. Périmètre : public 17 %, privé 68 %, bâti 10 %. Les meilleurs habitats à cèpes et girolles sont privés à ~91 % (lactaires 57 %).
  - Choix de l'utilisateur : pouvoir **alterner 0,3 / 0,7** pour le privé → bouton sur la carte (score embarqué sans foncier, facteur appliqué dans la page) ; email à `private_factor` = 0,3.
  - CI : résultats mis en cache (données publiques) ; recalcul (~1 Go de téléchargement) seulement si le code ou la config change.
- [ ] **Calibration** (≥ 20 sorties, bredouilles comprises) ← **prochaine étape : en attente des sorties**
- [ ] Mise en service de l'envoi quotidien : créer les secrets GitHub (cf. `.env.example`).

**Commandes :** `python -m radar score` (carte finale `web/index.html`) · `python -m radar report` · `python -m radar pied` · `python -m radar foncier` · `python -m radar controle-pluie` · `python -m radar journal` · `python -m radar v0` (météo seule, `web/v0.html`) · `python -m radar check-sources` · `python -m radar fetch` · `python -m radar terrain` · `.venv\Scripts\python -m pytest`

**Constats du run V0 (2026-11-02) :**
- 93 % des points sont « bon » ou « très bon » : la météo est quasi uniforme sur la zone (même épisode pluvieux ~19-20/10, délai 13-14 j). La V0 ne discrimine donc presque pas ; c'est attendu, la discrimination viendra de l'habitat (2b).
- Le délai mesuré (13-14 j) diffère des « ~8 j » du plan initial : le plan s'appuyait sur un site tiers à seuil inconnu ; le seuil de 20 mm sur 2 j de cette config détecte l'épisode du 19-20/10. À recaler avec le journal.
- Falaise du score au bord du plateau de délai (14 j) : le score peut chuter vite dans les jours suivants. Hypothèse à valider, pas un fait.
- 156 points mer/étang écartés via l'altitude 0 de l'API (approximation ; le masque terrain en 2a remplacera ça). Camargue basse (altitude ~1 m) reste scorée sans masque d'habitat.
- Pas de `make` sous Windows : remplacé par `python -m radar`.

**Décision (2026-11-02) : AROME abandonné**, on reste sur `best_match` à 5 km. La validation sur stations Météo-France (§7bis) reste possible plus tard si on veut requestionner le choix, mais n'est plus sur le chemin critique.

**Constats 2a (audit des données réelles, 2026-11-02)** — à prendre en compte dans 2b :
- **BD Forêt** : 25 374 (Gard) + 43 908 (Hérault) polygones, champs `CODE_TFV`, `TFV`, `TFV_G11`, `ESSENCE`. La part « essence précise » est faible : ESSENCE = *Feuillus* (21 227, générique), *NC* non classé (14 370) + *NR* (1 640), *Mixte* (13 493), *Conifères* (4 185) ; nommées : chênes sempervirents 3 703, pin d'Alep 2 160, chênes décidus 2 138, châtaignier 1 577, pin laricio/noir 929, hêtre 768, pin maritime 630, pins mélangés 577, douglas 575, sapin/épicéa 574, pin sylvestre 197. **Pas de pin parasol distinct.** Le champ `TFV` (37 valeurs) est parfois plus précis que `ESSENCE`.
  → **Rectification (2026-11-03, tableau croisé ESSENCE × TFV_G11)** : *NC* n'est **pas** une essence inconnue : NC = exclusivement Lande (LA4, 13 202) et Formation herbacée (LA6, 1 168) ; *NR* = forêt sans couvert arboré (FF0/FO0 : coupe rase, incendie, jeune peuplement). Les essences réellement *non précisées* sont « Feuillus » (FF1-00, FF1-00-00, FO1, FF1-49-49), « Conifères » génériques et « Mixte » (FF31/FF32/FO3). Le reclassement se fait donc sur **`CODE_TFV`** (29 classes, `config/terrain.yaml`), pas sur `ESSENCE`.
  → Conséquences pour 2b : (1) feuillus/conifères/mixtes non précisés = *essence inconnue* → score d'habitat intermédiaire explicite (prior), jamais 0 ; le seuil « sans essence adéquate, score 0 » du plan initial ne s'applique qu'aux essences **connues et défavorables** ; (2) landes/herbacés/hors forêt : habitat nul pour les familles forestières ; (3) cellules **hors couverture** (départements ≠ 30/34, 3 686 cellules) = habitat inconnu, à afficher comme tel.
- **Géologie BD Charm-50** : S_FGEOL = 20 139 (34) + 11 145 (30) polygones, **867 formations** en texte libre (`NOTATION`, `DESCR`). Reclassement par mots-clés (schiste/micaschiste/gneiss/granite → acide ; calcaire/dolomie/marne → calcaire ; alluvions/colluvions ; basalte ; hydro) avec **rapport de couverture** (part de surface classée vs « autre ») : on ne dira pas « sol acide » pour ce qu'on n'a pas classé.
- **Résultat du reclassement géologique (2026-11-03)** : calcaire 44 %, superficiel 32 %, détritique 14 %, acide 7 %, eau 2 %, basique 1 %, non classé **< 0,1 %** (1,7 km²). Règles : NOTATION (conventions BRGM) > mot-clé cité en premier dans DESCR > ajustement d'âge (grès/pélites **paléozoïques** → acide ; Permien exclu). Hypothèse régionale assumée : brèches → calcaire. Diagnostic qui a mené à ces règles : grès cambro-ordoviciens de Sumène d'abord classés « détritique » avec les molasses tertiaires (sol acide vs souvent calcaire), mots composés (« microgranites ») non détectés.
- **⚠️ Le plan initial est en partie démenti par les données** : Monoblet et le nord de Saint-Hippolyte-du-Fort sont sur **dolomies/calcaires du Lias-Jurassique** (78–86 % calcaire dans un rayon de 3 km), côté calcaire de la faille des Cévennes, pas sur schiste. Châtaignier dans 3 km : Sumène 15 % (socle acide 52 % : conforme), Saint-Hippolyte 5 %, Monoblet ≈ 0. → La « Définition de fini » de 2b est **à réécrire** : au lieu de « châtaigneraies sur schiste à Saint-Hippolyte, Sumène, Monoblet », un critère vérifiable sur les données (ex. les meilleures cellules « cèpes » se concentrent sur socle acide + châtaignier/chêne/hêtre ; Gardiole et garrigues ressortent pour la famille pins, pas pour les cèpes).
- **Encodages** : BD Forêt UTF-8 strict (apostrophe en U+0092 à normaliser) ; géologie cp1252. Projection source Lambert 93 (EPSG:2154) pour les deux.
- **MNT** : Copernicus GLO-30 (30 m, DSM) retenu plutôt que RGE ALTI (paquets départementaux très lourds) ; suffisant pour altitude/exposition à 250 m, pas pour des pentes fines (voir `SOURCES.md`).
- **Réserves** : BD Forêt date de 2018 (incendies/coupes postérieurs invisibles) ; miroir BRGM tiers faute d'accès officiel (403).

**Constats 2b (run du 2026-11-04, mois 11)** :
- Critères : (a) top 5 % cèpes = 91 % sol acide, 75 % châtaignier/chênes/hêtre ; (b) Gardiole lactaires 0,11 vs cèpes 0,09 — **marge faible** : la Gardiole est surtout garrigue/chêne vert, peu de pin d'Alep cartographié ; (c) Montpellier ≤ 0,001 ; (d) 3 686 cellules inconnues.
- Habitat médian des peuplements purs (≥ 70 %) : châtaignier 0,96 (cèpes) ; pin d'Alep 0,85 (lactaires) ; chêne vert 0,39 (cèpes : 0,8 essence × 0,5 calcaire) ; hêtre 0,75.
- Score final : 95–97 % des cellules « faible ». Deux causes : (1) **météo du jour en baisse** (médiane 50 contre 93 % « bon » le 2/11 : l'épisode du 19–20/10 sort du plateau de délai de 14 j — la « falaise » prévue) ; (2) l'habitat multiplie trois facteurs (essence × sol × altitude), ce qui pénalise les cas intermédiaires (chênaie verte sur calcaire). À juger sur le terrain avant de retoucher les poids.
- Curseur (run du 2026-11-04) : score météo médian 83 (J-7) → 79 (J-1) → **50 (J)** → ~41-47 (J+1..J+7). Le décrochage J-1 → J est la sortie du plateau de délai (14 j après l'épisode du 19–20/10) : falaise du modèle, à confronter au terrain. Cohérence vérifiée : J-2 (2/11) redonne les 93 % « bon+ » du run réel du 2/11.
- Point à surveiller : `exponent_habitat` = 1,0 rend le score très sélectif ; si les coins connus ressortent trop faibles, c'est le premier réglage à revoir (avec le poids calcaire des cèpes).

Dépôt public : https://github.com/ScipioH/champignons-radar
