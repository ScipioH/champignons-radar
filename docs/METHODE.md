# Méthode de construction de l'indicateur

Ce document décrit **comment est calculé le score de pousse** affiché sur la carte et dans le rapport. Toutes les
valeurs citées viennent de `config/config.yaml` (météo, accès) et `config/species.yaml` (habitat) : ce sont des
**hypothèses de cueilleur, non calibrées** tant que le journal compte moins de 20 sorties (voir §8).

> **Ce que l'indicateur mesure** : une probabilité relative que des champignons *poussent* à un endroit et à une
> date, d'après la météo récente, le terrain et la saison.
> **Ce qu'il ne mesure pas** : la comestibilité (jamais), la présence réelle (personne n'a compté), la pression
> de cueillette, l'autorisation d'accès d'une parcelle privée.

---

## Vue d'ensemble

Pour chaque **cellule de 250 m × 250 m**, chaque **famille** (cèpes, lactaires, girolles) et chaque **jour** du
curseur (J-7 à J+7) :

```
score = 100 × météo^0,6 × habitat^1,0 × saison × accès
```

| Facteur | Valeurs | Résolution | Varie avec |
|---|---|---|---|
| météo | 0 – 1 | points tous les 5 km, lissés sur les cellules | le jour |
| habitat | 0 – 1 | cellule 250 m | la famille (et le mois, via l'exposition) |
| saison | 1 / 0,5 / 0,1 | — | la famille et le mois |
| accès | 0 – 1, ou « hors périmètre » | cellule 250 m | fixe (calcul unique) |

C'est un **produit** : un facteur faible suffit à faire baisser le score (une forêt idéale après une sécheresse
reste faible ; une pluie parfaite sur une garrigue pelée aussi). L'exposant 0,6 sur la météo atténue sa sévérité,
parce que la météo modèle est moins sûre que le terrain ; l'habitat est pris tel quel (exposant 1, décision du
2026-11-04 : avec 0,4, une cellule à 10 % d'habitat gardait 40 % du score).

Classes affichées : **faible** < 35 ≤ **moyen** < 55 ≤ **bon** < 75 ≤ **très bon**.

---

## 1. Score météo (par point de la grille 5 km)

Source : Open-Meteo (modèle « best match »), séries quotidiennes de pluie et de température minimale, 40 jours
d'historique et 10 jours de prévision. Code : `radar/score.py`.

### 1.1 La pluie déclenchante

L'idée de départ : une poussée suit une **bonne pluie** (≥ 20 mm cumulés sur 2 jours consécutifs), après un délai
d'environ une à deux semaines. Le programme cherche la pluie déclenchante **la plus récente** dans les 30 derniers
jours ; le jour retenu est celui des deux qui a reçu le plus d'eau.

### 1.2 Cinq composantes, chacune entre 0 et 1

Toutes sont des rampes linéaires entre des seuils (plateau = 1).

| Composante | Poids | Ce qui est mesuré | Courbe |
|---|---|---|---|
| **pluie** | 0,30 | meilleur cumul sur 2 jours dans les 15 derniers jours | 0 sous 10 mm → 1 à 20 mm → plateau jusqu'à 60 mm → redescend à 0,5 à 150 mm (sols lessivés, ravinés) |
| **délai** | 0,25 | jours écoulés depuis la pluie déclenchante | 0 jusqu'à 4 j → 1 à 8 j → plateau jusqu'à 14 j → 0 à 35 j ; 0 s'il n'y a pas eu de pluie déclenchante |
| **température** | 0,20 | moyenne des minimales des 7 derniers jours | 0 à 3 °C ou moins → 1 à 8 °C ou plus |
| **sécheresse** | 0,15 | pluie moyenne par jour **depuis** la pluie déclenchante (le sol reste-t-il humide ?) | 0,4 à 0 mm/j → 1 à 1 mm/j ou plus ; 0,4 sans pluie déclenchante |
| **prévision** | 0,10 | cumul de pluie prévu sur les 3 jours suivants | 0,85 à sec → 1 de 5 à 40 mm → 0,8 à 80 mm ou plus (trop d'eau d'un coup) |

Quand une composante n'est pas calculable (pas de température, ou pas de prévision au bout de la série), elle est
ignorée et les poids des autres sont renormalisés.

### 1.3 Combinaison : moyenne géométrique pondérée avec plancher

```
météo_brute = exp( Σ poids_i × ln(max(composante_i ; 0,05)) / Σ poids_i )
```

- **Géométrique** plutôt qu'arithmétique : une composante mauvaise tire vraiment le score vers le bas (pas de pluie
  ne se compense pas par une bonne température).
- **Plancher 0,05** : une seule composante à 0 n'écrase pas tout à 0, ce qui garde un classement utile entre zones
  quand les conditions sont mauvaises partout.

### 1.4 Facteur gel (multiplicatif)

Sur les 7 derniers jours, on compte les nuits à 0 °C ou moins :

```
gel = max(0,2 ; 1 − 0,25 × nuits × m)     avec m = 1,5 si le point est au-dessus de 800 m, sinon 1
gel ≤ 0,1 si une nuit est descendue à −4 °C ou moins
```

Score météo du point = `100 × météo_brute × gel`.

---

## 2. De la grille 5 km aux cellules 250 m

Chaque cellule reçoit la **moyenne pondérée par l'inverse du carré de la distance** des 4 points météo les plus
proches (`habitat.idw_weights`). C'est un **lissage** pour éviter les marches d'escalier sur la carte, pas un gain
de précision : la météo reste connue à ~5 km près. Un orage localisé entre deux points n'est pas vu.

---

## 3. Habitat (par cellule 250 m et par famille)

```
habitat = forêt × géologie × altitude × exposition      (borné à 0 – 1)
```

Code : `radar/terrain.py` (préparation, une fois) et `radar/habitat.py` (calcul).

### 3.1 Préparation du terrain

Les polygones de **BD Forêt V2** (IGN, 2018) et de **BD Charm-50** (géologie BRGM 1/50 000) sont rasterisés à 50 m,
puis agrégés en cellules de 250 m sous forme de **pourcentages par classe** (une cellule peut être 60 %
châtaigneraie, 30 % chênaie verte, 10 % hors forêt). Le relief vient du **Copernicus DEM 30 m** : altitude moyenne,
et exposition moyenne pondérée par la pente (un replat ne compte pas).

### 3.2 Forêt et géologie : moyenne des poids pondérée par les surfaces

```
forêt = Σ (pourcentage de la classe × poids de la classe pour la famille) / Σ pourcentages
```

Même formule pour la géologie. Extraits des poids (tableau complet dans `species.yaml`) :

| Classe | Cèpes | Lactaires | Girolles |
|---|---|---|---|
| châtaignier | 1,0 | 0 | 1,0 |
| hêtre | 0,9 | 0 | 0,8 |
| chênes (vert / décidu) | 0,8 / 0,8 | 0 | 0,6 / 0,7 |
| pin maritime, sylvestre | 0,5 | 0,8 | 0,5 |
| pin d'Alep | 0,1 | 1,0 | 0,1 |
| hors forêt, lande, herbacé | 0 | 0 | 0 |
| sol acide / calcaire | 1,0 / 0,5 | 0,8 / 1,0 | 1,0 / 0,3 |

Règles particulières :

- **Forêt ouverte** = poids de la forêt fermée correspondante × 0,7 (cèpes, girolles) ou 0,8 (lactaires).
- **Essence non reconnue** : poids 0,4 (prior intermédiaire, jamais 0). **Formation géologique non classée** : 0,6.
- **Eau, zones urbanisées** : 0.
- **Hors couverture géologique** (hors Hérault et Gard) : habitat **inconnu**, affiché comme tel. Ce n'est pas la
  même chose qu'un habitat nul.
- Le programme refuse de tourner si une classe de terrain n'a pas de poids, pour éviter un zéro silencieux.

### 3.3 Altitude

Facteur 1 dans la plage idéale, puis rampe linéaire sur `ramp_m` jusqu'à un plancher `edge` :

| Famille | Plage idéale | Rampe | Plancher |
|---|---|---|---|
| cèpes, girolles | 200 – 1 200 m | 300 m | 0,5 |
| lactaires | 0 – 900 m | 400 m | 0,4 |

Exemple : un cèpe à 1 400 m reçoit `0,5 + 0,5 × (1 500 − 1 400) / 300 = 0,67`.

### 3.4 Exposition (effet faible)

En septembre et octobre seulement, les versants frais (nord-est) sont favorisés et les versants sud-ouest pénalisés,
de **±10 % au maximum** (atteint pour une pente d'environ 20°). Le reste de l'année, ce facteur vaut 1.

---

## 4. Saison

| Famille | Mois de saison (×1) | Mois adjacents (×0,5) | Reste (×0,1) |
|---|---|---|---|
| cèpes | sept. – nov. | août, déc. | le reste |
| lactaires | oct. – déc. | sept., janv. | le reste |
| girolles | juin – nov. | mai, déc. | le reste |

Sur le curseur temporel, chaque jour utilise **son propre mois** : le 30 novembre et le 1er décembre n'ont pas le même
facteur pour les cèpes.

---

## 5. Accès (calcul unique)

Code : `radar/access.py`, commande `python -m radar access`.

- **Temps de trajet** : depuis le centre de Montpellier, ou depuis le domicile si `RADAR_HOME_LAT/LON` sont définis
  en local. Durée en voiture calculée par OSRM (réseau routier OpenStreetMap) vers des points tous les 2 km, plus la
  marche depuis la route la plus proche à 4 km/h, puis lissage sur les cellules. **Au-delà de 60 min, la cellule est
  retirée de la carte** (`availability.max_travel_minutes`). Les bouchons ne sont pas pris en compte.
- **Zones protégées** (couches PatriNat, MNHN/OFB) : le score est multiplié par
  `Π (1 − part de la cellule dans la zone × (1 − facteur))`.
  - Réserves naturelles nationales et régionales, arrêtés de biotope : facteur 0. Une cellule entièrement dedans
    tombe à 0, une cellule à moitié dedans est divisée par 2.
  - Cœur du Parc national des Cévennes : facteur 1, donc aucun effet sur le score. La zone est **signalée** comme
    « cueillette réglementée ».
  - Dans tous les cas, c'est le règlement de chaque site qui fait foi.

---

## 6. Exemple chiffré

Une cellule en octobre : 60 % châtaigneraie et 40 % chênaie verte, sol 100 % acide, 500 m d'altitude, terrain plat.
Côté météo : 35 mm tombés il y a 10 jours, plus rien depuis, minimales moyennes à 9 °C, 10 mm prévus, pas de gel.

| Étape | Calcul | Valeur |
|---|---|---|
| pluie | 35 mm, sur le plateau 20 – 60 | 1,0 |
| délai | 10 jours, sur le plateau 8 – 14 | 1,0 |
| température | 9 °C ≥ 8 | 1,0 |
| sécheresse | 0 mm/j depuis la pluie | 0,4 |
| prévision | 10 mm, sur le plateau 5 – 40 | 1,0 |
| météo | 0,4^0,15 (seule la sécheresse pèse) | 0,872 |
| forêt (cèpes) | 0,6 × 1,0 + 0,4 × 0,8 | 0,92 |
| habitat | 0,92 × 1,0 (géologie) × 1 (altitude) × 1 (exposition) | 0,92 |
| saison | octobre, en saison | 1,0 |
| **score** | 100 × 0,872^0,6 × 0,92 × 1 × 1 | **85 → très bon** |

La même cellule pour les lactaires : forêt 0 (pas de pins), donc **score 0**. Pour une pinède d'Alep sur calcaire,
c'est l'inverse.

---

## 7. Du score au rapport

`python -m radar report` (`radar/report.py`, `radar/calendar_fr.py`) :

1. **Créneaux** de J à J+7 : samedis, dimanches, jours fériés (Pâques calculée, jours fériés français). Un soir de
   semaine compte seulement s'il reste au moins 60 min de jour sur place en partant à 17 h, avec l'heure de coucher
   du soleil et le temps de trajet.
2. **Zones** : pour chaque créneau et chaque famille, on prend les 3 meilleures cellules, en écartant toute cellule
   à moins de 5 km d'une zone déjà retenue. Sinon les 3 zones proposées seraient côte à côte.
3. Chaque zone est décrite par sa forêt et son sol dominants, son altitude, son temps de trajet et la mention
   « réglementé » s'il y a lieu.

---

## 8. Validation et calibration

**Ce qui a été vérifié** : la cohérence du code avec les hypothèses, sur le vrai terrain (critères automatiques de
`species.yaml`) :

- (a) le top 5 % des cellules à cèpes est à 91 % sur socle acide et à 75 % en châtaignier, chênes ou hêtre ;
- (b) le massif de la Gardiole favorise les lactaires plutôt que les cèpes ;
- (c) le centre de Montpellier est proche de 0 ;
- (d) les zones inconnues sont bien marquées comme inconnues.

Ces critères **ne prouvent pas que les hypothèses sont justes**.

**Ce qui le prouvera** : le journal de sorties (`Journal.bat`). À chaque sortie, le programme enregistre le score
que le modèle donne **pour ce jour et cet endroit**, avec la version de la configuration. La météo est
reconstituée par Open-Meteo : prévisions et analyses récentes jusqu'à 85 jours, réanalyse au-delà. Le facteur
d'accès n'entre pas dans ce score, qui évalue la pousse et non le trajet.

> Réserve : pour une sortie passée, la composante « prévision » utilise la pluie **réellement tombée** dans les
> 3 jours suivants, pas celle qui était prévue ce jour-là. Elle ne pèse que 10 %, mais elle avantage légèrement le
> modèle.

Il mesure ensuite :

- la **part des sorties avec au moins quelques champignons** et le résultat moyen (0 – 3), par classe prédite.
  Un modèle utile donne « très bon » > « bon » > « moyen » > « faible » ;
- la **corrélation de rang de Spearman** entre le score et le résultat (> 0 : bon sens ; proche de 0 : le modèle ne
  discrimine pas).

En dessous de 20 sorties, l'indice reste marqué « non calibré ». Les sorties **bredouilles** sont indispensables :
sans elles, on ne mesure que les succès.

---

## 9. Limites connues

- **Météo modèle**, pas observée, à 5 km de résolution : les orages cévenols très localisés sont lissés. J-7 à J-1
  correspondent aux analyses du modèle ; au-delà de J+3, la prévision devient peu fiable.
- **Pas d'humidité du sol ni d'évaporation** : la « sécheresse » est approchée par la pluie tombée depuis la pluie
  déclenchante. Le vent et le soleil, qui assèchent vite, ne sont pas vus.
- **Délai fixe en jours**, indépendant de la température, alors que la pousse est plus rapide par temps doux.
- **Pas de borne haute de température** : une canicule ne pénalise pas, seule la sécheresse le fait indirectement.
- **BD Forêt date de 2018** : les incendies, coupes et dépérissements postérieurs ne sont pas vus. Les essences
  précises ne sont renseignées que sur une minorité de polygones, d'où les priors.
- **Toutes les valeurs sont des hypothèses** : poids, seuils et exposants n'ont pas encore été ajustés sur des
  observations.

---

## Où modifier quoi

| Je veux changer… | Fichier | Clé |
|---|---|---|
| seuils et poids météo | `config/config.yaml` | `score` |
| poids forêt / sol / altitude d'une famille | `config/species.yaml` | `families.<famille>` |
| mois de saison | `config/species.yaml` | `season_months`, `season` |
| temps de trajet maximal | `config/config.yaml` | `availability.max_travel_minutes` |
| traitement des zones protégées | `config/config.yaml` | `access.protected.layers.*.factor` |
| bornes des classes | `config/config.yaml` | `score.classes` |

Toute modification change la « version du modèle » enregistrée dans le journal : les sorties restent comparables
entre elles. La commande `python -m radar journal recalculer` réévalue avec la nouvelle configuration les sorties
enregistrées sous une ancienne version, et celles dont le score n'avait pas pu être calculé.
