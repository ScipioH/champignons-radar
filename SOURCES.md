# Sources de données — vérifiées le 2026-11-02

Règle du projet : aucune URL n'est utilisée sans avoir été testée. Les URL, tailles et sommes de contrôle
sont dans `config/sources.yaml` ; les SHA-256 réels des fichiers téléchargés vont dans `data/raw/CHECKSUMS.json`
(non versionné). Télécharger : `python -m radar fetch`. Auditer : `python -I tools/audit_terrain.py data/raw`.

| Donnée | Fournisseur | Licence | Vintage | Taille | Intégrité |
|---|---|---|---|---|---|
| **BD Forêt® V2.0** — Hérault (34) et Gard (30), shapefile Lambert 93 | IGN, Géoplateforme (`data.geopf.fr/telechargement/resource/BDFORET`) | Licence Ouverte 2.0 (miroirs data.gouv : `lov2`) | D034 : 2018-01-15 ; D030 : 2018-11-20 | 63 Mo + 33 Mo | taille + MD5 publiés, contrôlés |
| **BD Charm-50** (géologie 1/50 000 harmonisée) — 34 et 30, shapefile Lambert 93 | BRGM, via le miroir `data.cquest.org` (voir réserve) | Licence Ouverte 2.0 (`A_LIRE.TXT` du miroir) | 2019-2021 | 31 Mo + 23 Mo | taille contrôlée ; pas de MD5 publié → SHA-256 enregistré |
| **Copernicus DEM GLO-30** (30 m) — 4 tuiles N43/N44 × E003/E004 | ESA / Copernicus, bucket AWS Open Data `copernicus-dem-30m` | Libre et ouverte (attribution Copernicus requise) | — | 147 Mo | taille contrôlée ; SHA-256 enregistré |
| **Observations quotidiennes** (contrôle des pluies, `radar controle-pluie`) — fichier `latest` 34 et 30 | Météo-France via data.gouv.fr, dataset `6569b51ae64326786e4e8e1a` (URL lue dans le catalogue `www.data.gouv.fr/api/1/datasets/…`, hôtes autorisés listés dans la config) | Licence Ouverte 2.0 | ~J-2 (vérifié le 2026-11-07 : relevés jusqu'au 5/11 ; la date « modifié » du catalogue est en retard sur le contenu) | ~0,55 Mo / département | gzip contrôlé, taille plafonnée, colonnes vérifiées, valeurs bornées, QRR = 2 (douteux) écarté |
| **Voies (routes, chemins, sentiers)** — accès à pied, `radar pied` | IGN, BD TOPO® V3, WFS Géoplateforme `BDTOPO_V3:troncon_de_route` (par dalles de 10 km, découpées si > 5 000 entités) | Licence Ouverte 2.0 | testée le 2026-11-11 : ~545 000 tronçons sur l'emprise | ~300 Mo de JSON, non conservé (raster 50 m en cache dans `data/raw/paths`) | réponse JSON contrôlée (liste d'entités, types de géométrie) |
| **Parcelles et bâtiments du cadastre** — foncier, `radar foncier` | Etalab, `cadastre.data.gouv.fr/data/etalab-cadastre/2026-09-01/geojson/departements/{34,30}/` | Licence Ouverte 2.0 | millésime 2026-09-01 (daté : « latest » change chaque trimestre) | 194 + 171 Mo (parcelles), 55 + 41 Mo (bâtiments) | taille contrôlée ; pas de MD5 publié → SHA-256 enregistré |
| **Parcelles des personnes morales** — foncier | DGFiP via data.economie.gouv.fr (dataset data.gouv `605d268f4661cf23272817c3`), situation au 1er janvier 2026, zips « dpts 1 à 30 » et « 31 à 60 » | Licence Ouverte 2.0 | 2026 (publié le 2026-11-03) | 128 + 146 Mo, lus sans extraction | taille contrôlée ; colonnes repérées par leur libellé ; seuls les CSV 300 et 340 sont lus |
| **Météo modèle** | Open-Meteo.com : `meteofrance_seamless` (principal, jusqu'à J+3) complété par `best_match` | CC BY 4.0, usage non commercial | temps réel | — | — |
| **Prévisions archivées** (contrôle des pluies, journal au-delà de ~40 j) | Open-Meteo, `historical-forecast-api.open-meteo.com` | CC BY 4.0 | testée le 2026-11-07 : 91 jours complets pour les deux modèles | — | — |

## Réserves à connaître

- **Antivirus qui analyse le HTTPS** (constaté le 2026-11-11 sur le PC local) : les certificats sont ré-signés par
  « Avast Web/Mail Shield » et, par moments, présentés comme expirés. Python refuse alors la connexion. Le programme
  échoue tout de suite avec un message explicite et la carte utilise la dernière météo en cache. Solution : exclure
  `*.open-meteo.com` (et `data.gouv.fr`, `meteofrance.s3.sbg.io.cloud.ovh.net`) de l'analyse HTTPS de l'antivirus.
  Ne jamais désactiver la vérification des certificats dans le code. La CI GitHub n'est pas concernée.

- **Propriétaires** : le fichier DGFiP ne recense que les personnes morales (sociétés, collectivités…), pas les
  particuliers. Une parcelle est publique si son propriétaire est dans les groupes 1 (État), 2 (région),
  3 (département), 4 (commune), 9 (établissement public), ou a une forme juridique de droit public (7xxx). Ce
  dernier cas rattrape des collectivités classées en groupe 0, par exemple des sections de commune. Aucun nom de
  particulier n'est lu ni stocké. Seuls les identifiants de parcelles publiques servent, en mémoire. Cette source
  fait apparaître beaucoup plus de terrain public que la couche ONF des forêts publiques (~5 % du périmètre).

- **Historique de l'API de prévision Open-Meteo** : au-delà d'environ 43 jours (`meteofrance_seamless`) ou 57 jours
  (`best_match`), elle renvoie des valeurs vides sans erreur (constaté le 2026-11-07 avec `past_days=90`). La carte
  utilise 40 jours, sans trou ; le contrôle des pluies et les sorties anciennes du journal passent par les
  prévisions archivées.

- **Miroir BRGM** : l'URL officielle (`infoterre.brgm.fr/telechargements/BDCharm50/…`) renvoie **403** (testée le 2026-11-02, avec GET et HEAD). Le miroir `data.cquest.org` (OpenStreetMap France) sert les mêmes fichiers en HTTPS, avec la licence et l'URL d'origine indiquées. Sans MD5 du fournisseur, l'intégrité repose sur la taille et un SHA-256 local : si le BRGM rétablit l'accès, comparer.
- **Âge de BD Forêt** : 2018, soit ~8 ans. Incendies, coupes et dépérissements postérieurs ne sont pas vus.
- **Précision des essences** : seule une minorité de polygones nomme l'essence (voir PLAN.md, constats 2a).
- **Copernicus GLO-30 est un modèle de surface (DSM)**, pas de terrain : la canopée gonfle l'altitude de plusieurs mètres en forêt. Négligeable pour des cellules de 250 m (altitude, exposition), à ne pas utiliser pour des pentes fines. RGE ALTI® (IGN, 1 m/5 m, Licence Ouverte 2.0, MNT vrai) existe en paquets départementaux très lourds ; à reconsidérer si la précision l'exige.
- **Catalogue IGN** : geoservices.ign.fr redirige désormais vers cartes.gouv.fr (application JavaScript non lisible par script) ; le catalogue de téléchargement fiable est le flux Atom `data.geopf.fr/telechargement/resource/<PRODUIT>`.

## Mentions à reprendre sur la carte et le rapport

Données météo : Open-Meteo.com (CC BY 4.0). Forêt : © IGN – BD Forêt® (Licence Ouverte 2.0). Géologie : © BRGM – BD Charm-50 (Licence Ouverte 2.0). Relief : © ESA / Copernicus DEM GLO-30. Observations : © Météo-France (Licence Ouverte 2.0). Voies : © IGN – BD TOPO® (Licence Ouverte 2.0). Cadastre :
© DGFiP / Etalab (Licence Ouverte 2.0). Fonds de carte : © IGN Géoplateforme – Plan IGN et photographies aériennes (Licence Ouverte 2.0, sans clé) ; © OpenStreetMap / OpenTopoMap (CC-BY-SA).

**Fonds de carte écartés** (la carte est ouverte en local, `file://`, sans en-tête Referer ; constaté le 2026-11-04) : `tile.openstreetmap.org` renvoie « Access blocked » (politique d'usage OSM) ; CARTO basemaps renvoie une tuile « API KEY REQUIRED ». Vérifier visuellement une tuile, pas seulement le code HTTP (200 dans les deux cas).
