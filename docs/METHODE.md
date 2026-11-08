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

Source : Open-Meteo, séries quotidiennes de pluie, de température minimale et d'évapotranspiration de référence
(ET0), avec 40 jours d'historique et 10 jours de prévision. Code : `radar/score.py`, `radar/weather.py`.

**Modèle météo** : modèle principal **Météo-France** (`meteofrance_seamless`, AROME 1,3 km + ARPEGE), retenu le
2026-11-07 après contrôle sur les stations (§8, « Contrôle des pluies »). Il ne prévoit que jusqu'à J+3 ; au-delà, et pour tout jour sans
valeur, la série est complétée jour par jour par le modèle « best match » d'Open-Meteo.

### 1.1 La pluie déclenchante

L'idée de départ : une poussée suit une **bonne pluie** (≥ 20 mm cumulés sur 2 jours consécutifs), après un délai
d'environ une à deux semaines. Le programme repère les pluies déclenchantes des 30 derniers jours. Des jours de
pluie consécutifs forment un seul épisode, daté de son jour le plus arrosé.

**Enchaînement des pluies** (règles du 2026-11-08) :

- **Pluie modérée** (moins de 50 mm sur 2 jours) pendant une poussée encore active, c'est-à-dire au plus 14 jours
  après sa dernière pluie : la poussée est **prolongée**. La montée du délai reste comptée depuis le début de la
  poussée ; le déclin est compté depuis cette dernière pluie.
- **Forte pluie** (50 mm ou plus sur 2 jours) : une **nouvelle poussée** démarre. L'ancienne est considérée comme
  **abîmée** (champignons gorgés d'eau, limaces) et garde la moitié de son score météo, tant que c'est mieux que la
  nouvelle.
- Pluie modérée après une poussée éteinte : nouvelle poussée.

Constat sur les données de novembre 2026 : la moitié du score d'une poussée mûre (environ 80 / 2 = 40) est proche
de ce que donne déjà le plancher des composantes avec un délai nul (environ 42). La règle « poussée abîmée » rend
donc ce comportement explicite et réglable, sans changer beaucoup les chiffres à 0,5.

### 1.2 Cinq composantes, chacune entre 0 et 1

Toutes sont des rampes linéaires entre des seuils (plateau = 1).

| Composante | Poids | Ce qui est mesuré | Courbe |
|---|---|---|---|
| **pluie** | 0,30 | meilleur cumul sur 2 jours dans les 15 derniers jours | 0 sous 10 mm → 1 à 20 mm → plateau jusqu'à 60 mm → redescend à 0,5 à 150 mm (sols lessivés, ravinés) |
| **délai** | 0,25 | âge de la poussée en cours (§1.1) | montée depuis son début : 0 jusqu'à 4 j → 1 à 8 j ; déclin depuis sa dernière pluie : plateau jusqu'à 14 j → 0 à 35 j ; 0 sans pluie déclenchante |
| **température** | 0,20 | moyenne des minimales des 7 derniers jours | 0 à 3 °C ou moins → 1 à 8 °C ou plus |
| **sécheresse** | 0,15 | réserve d'eau du sol au jour J, par bilan pluie − évaporation (§1.3) | 0,4 si la réserve est à 10 % ou moins → 1 à partir de 50 % |
| **prévision** | 0,10 | cumul de pluie prévu sur les 3 jours suivants | 0,85 à sec → 1 de 5 à 40 mm → 0,8 à 80 mm ou plus (trop d'eau d'un coup) |

Quand une composante n'est pas calculable (pas de température, ou pas de prévision au bout de la série), elle est
ignorée et les poids des autres sont renormalisés.

### 1.3 Assèchement : bilan d'eau du sol

Une réserve de surface (litière et premiers centimètres, capacité 40 mm) est suivie jour après jour depuis le début
de la série :

```
réserve = min(réserve + pluie du jour ; 40 mm) − 0,7 × ET0 du jour        (jamais sous 0)
```

- **ET0** est l'évapotranspiration de référence calculée par Open-Meteo à partir du soleil, du vent, de
  l'humidité et de la température. Le coefficient 0,7 tient compte de l'abri du couvert forestier.
- Conséquence : 35 mm suivis de 10 jours secs laissent un sol encore humide en novembre (ET0 vers 1 mm/j), mais
  presque sec en septembre (ET0 vers 4 mm/j). L'ancienne mesure (pluie moyenne depuis l'averse) donnait le même
  résultat dans les deux cas.
- La réserve démarre à 50 % de sa capacité. Cet état initial est oublié au bout de quelques semaines de série.
- Si l'ET0 manque, le programme revient à l'ancienne mesure : pluie moyenne par jour depuis la pluie
  déclenchante, 0,4 à 0 mm/j et 1 à partir de 1 mm/j.

### 1.4 Combinaison : moyenne géométrique pondérée avec plancher

```
météo_brute = exp( Σ poids_i × ln(max(composante_i ; 0,05)) / Σ poids_i )
```

- **Géométrique** plutôt qu'arithmétique : une composante mauvaise tire vraiment le score vers le bas (pas de pluie
  ne se compense pas par une bonne température).
- **Plancher 0,05** : une seule composante à 0 n'écrase pas tout à 0, ce qui garde un classement utile entre zones
  quand les conditions sont mauvaises partout.

### 1.5 Facteur gel (multiplicatif)

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
  marche depuis la route la plus proche à 4 km/h, puis lissage sur les cellules. **Au-delà de 75 min, la cellule est
  retirée de la carte** (`availability.max_travel_minutes`, porté de 60 à 75 min le 2026-11-07 pour inclure les
  Cévennes). Les bouchons ne sont pas pris en compte.
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
Côté météo : 35 mm tombés il y a 10 jours, plus rien depuis, réserve du sol retombée à 30 %, minimales moyennes à
9 °C, 10 mm prévus, pas de gel.

| Étape | Calcul | Valeur |
|---|---|---|
| pluie | 35 mm, sur le plateau 20 – 60 | 1,0 |
| délai | 10 jours, sur le plateau 8 – 14 | 1,0 |
| température | 9 °C ≥ 8 | 1,0 |
| sécheresse | réserve 30 %, à mi-chemin entre 10 % et 50 % | 0,7 |
| prévision | 10 mm, sur le plateau 5 – 40 | 1,0 |
| météo | 0,7^0,15 (seule la sécheresse pèse) | 0,948 |
| forêt (cèpes) | 0,6 × 1,0 + 0,4 × 0,8 | 0,92 |
| habitat | 0,92 × 1,0 (géologie) × 1 (altitude) × 1 (exposition) | 0,92 |
| saison | octobre, en saison | 1,0 |
| **score** | 100 × 0,948^0,6 × 0,92 × 1 × 1 | **89 → très bon** |

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
reconstituée par Open-Meteo avec le même mélange de modèles que la carte : API de prévision pour les sorties
récentes (jusqu'à environ 2 jours), prévisions archivées des mêmes modèles au-delà. Le facteur d'accès n'entre pas
dans ce score, qui évalue la pousse et non le trajet.

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

### Contrôle des pluies

`python -m radar controle-pluie` (`radar/observations.py`) confronte la pluie des modèles aux relevés quotidiens
des stations Météo-France de l'Hérault et du Gard situées à moins de 70 km. Ces relevés ont environ 2 jours de
retard ; seuls les relevés validés ou non encore expertisés sont gardés, les douteux sont écartés.

- **Période** : les 90 derniers jours de relevés. Les modèles sont lus dans les prévisions archivées d'Open-Meteo
  (« historical forecast »), et tous sont comparés exactement sur les mêmes jours.
- **Mesures** :
  - le rapport des cumuls modèle / observé ;
  - la corrélation jour par jour ;
  - les pluies déclenchantes vues, manquées ou inventées, avec une tolérance de ±1 jour ;
  - l'**effet sur le score météo** : le même calcul est refait avec la pluie observée à la place de celle du
    modèle. La température et l'ET0 restent celles du modèle, pour isoler l'effet de la pluie.
- **Résultat du 2026-11-07** (44 stations, 8 août – 5 novembre 2026) :

| | best match | Météo-France |
|---|---|---|
| cumul modèle / observé | 0,66 | 0,98 |
| pluies déclenchantes vues | 64 % | 81 % |
| fausses alertes | 9 % | 7 % |
| écart moyen sur le score météo | 12,0 pts | 6,7 pts |
| classe météo différente de celle obtenue avec les stations | 23 % | 12 % |

« best match » sous-estimait nettement la pluie, surtout en plaine (cumul à 0,61). D'où le passage au modèle
Météo-France. Une station est un point et une maille de modèle une surface : il faut regarder les tendances, pas
une station isolée. Le contrôle se relance quand on veut, par exemple en fin de saison.

---

## 9. Limites connues

- **Météo modèle**, pas observée, échantillonnée tous les 5 km : les orages cévenols très localisés sont lissés.
  J-7 à J-1 correspondent aux analyses du modèle. Au-delà de J+3, la série vient d'un autre modèle, moins fin et
  sous-estimant la pluie en plaine : ces jours sont les moins fiables.
- **Bilan d'eau simplifié** : capacité et coefficient sont des hypothèses, sans type de sol ni ruissellement. Une
  forte pente ou un sol filtrant (sable, éboulis) sèchent plus vite que prévu.
- **Enchaînement des pluies schématique** : le seuil de 50 mm et le facteur 0,5 sont des hypothèses de cueilleur.
  L'intensité horaire, la grêle ou le froid qui suit ne sont pas pris en compte. Les sorties juste après une grosse
  pluie, avec une note sur l'état des champignons, sont les plus utiles pour les régler.
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
| modèle météo et complément | `config/config.yaml` | `weather.model`, `weather.fallback_model` |
| bilan d'eau du sol | `config/config.yaml` | `score.secheresse` |
| forte pluie qui relance, poussée abîmée | `config/config.yaml` | `score.delai.reset_mm`, `score.delai.damaged_factor` |
| traitement des zones protégées | `config/config.yaml` | `access.protected.layers.*.factor` |
| bornes des classes | `config/config.yaml` | `score.classes` |

Toute modification change la « version du modèle » enregistrée dans le journal : les sorties restent comparables
entre elles. La commande `python -m radar journal recalculer` réévalue avec la nouvelle configuration les sorties
enregistrées sous une ancienne version, et celles dont le score n'avait pas pu être calculé.
