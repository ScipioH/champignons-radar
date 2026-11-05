# Pistes d'amélioration

État au 2026-11-05 : les phases 1 à 4 sont livrées. Le principal manque n'est pas technique : le modèle n'a encore
jamais été confronté au terrain. Les pistes sont classées par intérêt, avec un effort indicatif.

---

## A. Rendre l'indicateur plus juste

| # | Piste | Pourquoi | Effort |
|---|---|---|---|
| A1 | **Humidité du sol et évapotranspiration** (Open-Meteo : `soil_moisture_*`, `et0_fao_evapotranspiration`) à la place de la composante « sécheresse » | Aujourd'hui, l'assèchement est déduit de la seule pluie. Le vent et le soleil, décisifs en garrigue, ne sont pas vus. | moyen |
| A2 | **Délai en degrés-jours** plutôt qu'en jours | Par temps doux, la pousse démarre plus tôt. Un délai fixe de 8 à 14 jours est faux en septembre comme en décembre. | faible |
| A3 | **Pénalité de chaleur** (minimales très élevées combinées à la sécheresse) | Aucune borne haute de température n'existe pour l'instant. Utile surtout pour les girolles en été. | faible |
| A4 | **Contrôle de la pluie modèle** par les observations Météo-France (jeu de données déjà repéré dans SOURCES.md) | Si le modèle sous-estime les épisodes cévenols, tout le score suit. Il faut le mesurer avant d'y toucher. | moyen |
| A5 | **Modèle AROME 1,3 km** (`weather.model`, la commande `compare-models` existe) | Meilleure résolution des orages localisés. Coût : prévision limitée à environ 2 jours, donc il faudrait la combiner avec un modèle global. | moyen |
| A6 | **Incertitude des prévisions** (API d'ensemble Open-Meteo) | Afficher « J+5 : bon, mais incertain » au lieu d'un chiffre trompeur. | moyen |
| A7 | **Mise à jour de la forêt** : surfaces brûlées depuis 2018 (base Prométhée / EFFIS) | BD Forêt date de 2018. Une pinède brûlée y reste « idéale lactaires ». | moyen |

## B. Calibrer (dès 20 à 30 sorties)

| # | Piste | Pourquoi | Effort |
|---|---|---|---|
| B1 | **Ajuster les seuils et les poids** sur le journal, par une régression ordinale simple avec validation croisée | Remplacer les hypothèses par des valeurs mesurées. Attention au surapprentissage avec peu de sorties : on ne touche qu'à quelques paramètres. | moyen |
| B2 | **Archiver la prévision du jour** dans le rapport quotidien | Pour une sortie passée, la composante « prévision » utilise aujourd'hui la pluie réellement tombée (léger avantage indu au modèle). Archiver ce qui était prévu rend l'évaluation honnête. | faible |
| B3 | **Saisie depuis le téléphone** : import d'un fichier GPX ou d'un point partagé | Le journal se remplit au PC, donc souvent de mémoire et avec des oublis, surtout pour les sorties bredouilles. | moyen |

## C. Usage au quotidien

| # | Piste | Pourquoi | Effort |
|---|---|---|---|
| C1 | **Seuil de trajet à 75 min** (`availability.max_travel_minutes`) | Les meilleures zones à cèpes (Cévennes, vers Sumène) sont juste au-delà de 60 min. | immédiat |
| C2 | **Email seulement s'il y a du nouveau** : une zone passe en « bon » ou plus sur un créneau, ou forte variation depuis la veille | Un email par jour finit par ne plus être lu. | faible |
| C3 | **Lien vers une carte en ligne** depuis l'email (GitHub Pages) | La carte interactive est aujourd'hui locale. À ne faire **qu'avec le centre de Montpellier comme origine**, sinon le contour à 60 min révèle le domicile. | moyen |
| C4 | **Jours de battue** par département (calendriers des fédérations de chasse) | Le rapport rappelle seulement la période de chasse en général. | moyen, données à trouver |
| C5 | **Vigilance Météo-France automatique** | C'est aujourd'hui un simple rappel. L'API officielle demande une clé gratuite, à stocker en secret. | faible |

## D. Entretien

- **Actions GitHub** : mettre à jour les versions (avertissement Node 20) et suivre le passage d'ubuntu-latest à
  Ubuntu 26.
- **Suspension après 60 jours sans commit** sur un dépôt public : la réactivation se fait d'un clic. Un petit
  workflow mensuel peut aussi l'empêcher.
- **OSRM de démonstration** : service sans garantie. Le temps de trajet étant calculé une seule fois, le risque est
  faible.

---

## Ordre conseillé

1. **C1**, puis des sorties notées, y compris bredouilles : c'est la seule façon de savoir si le reste vaut l'effort.
2. **A1 + A2** : ce sont les deux simplifications les plus fortes du modèle actuel, et les données sont déjà
   accessibles.
3. **B2**, tôt, pour que l'évaluation future soit propre.
4. **C2** si les emails deviennent du bruit.
5. **B1** une fois 20 à 30 sorties réunies.
