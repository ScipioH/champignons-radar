# Pistes d'amélioration

État au 2026-11-07 : les phases 1 à 4 sont livrées, ainsi que les premières améliorations (section « Fait »). Le
principal manque n'est pas technique : le modèle n'a encore jamais été confronté au terrain. Les pistes restantes
sont classées par intérêt, avec un effort indicatif.

---

## Fait le 2026-11-07

- **A1, assèchement** : un bilan d'eau quotidien du sol (pluie − 0,7 × ET0, réserve de 40 mm) remplace « pluie
  moyenne depuis l'averse ». Voir METHODE.md, §1.3.
- **A4, contrôle des pluies** : la commande `python -m radar controle-pluie` compare les modèles à 44 stations
  Météo-France sur 90 jours.
- **A5, modèle Météo-France** : adopté comme modèle principal à la suite de ce contrôle. Le cumul passe de 0,66 à
  0,98 de l'observé, les pluies déclenchantes vues de 64 % à 81 %, et l'erreur sur le score de 12 à 6,7 points.
  Au-delà de J+3, la série est complétée par best match.
- **C1, trajet** : 75 min au lieu de 60.
- **A8, enchaînement des pluies** (2026-11-08) : une pluie modérée (moins de 50 mm sur 2 jours) pendant une
  poussée la prolonge au lieu de la relancer. Une forte pluie relance une poussée, et l'ancienne, « abîmée », garde
  la moitié de son score. Voir METHODE.md, §1.1.
- **Accès à pied** (2026-11-11) : la pente (15° → 30°) et l'éloignement des chemins IGN atténuent le score,
  au pire de moitié. Voir METHODE.md, §5.
- **Foncier** (2026-11-11) : les abords bâtis (moins de 30 m d'un bâtiment) sont retirés, le terrain privé est
  atténué (× 0,3), le terrain public (État, communes, établissements publics, d'après la DGFiP) reste entier.
- **Robustesse** : si Open-Meteo est injoignable, la carte est calculée avec la dernière météo téléchargée, et un
  avertissement rouge l'indique en haut de la carte.

---

## A. Rendre l'indicateur plus juste

| # | Piste | Pourquoi | Effort |
|---|---|---|---|
| A2 | **Délai en degrés-jours** plutôt qu'en jours | Par temps doux, la pousse démarre plus tôt. Un délai fixe de 8 à 14 jours est faux en septembre comme en décembre. | faible |
| A3 | **Pénalité de chaleur** (minimales très élevées combinées à la sécheresse) | Aucune borne haute de température n'existe pour l'instant. Utile surtout pour les girolles en été. | faible |
| A9 | **Corriger best match en plaine pour J+4..J+10** (facteur mesuré par `controle-pluie`) ou n'afficher ces jours qu'à titre indicatif | Ce modèle sous-estime la pluie d'environ 40 % en plaine. | faible |
| A6 | **Incertitude des prévisions** (API d'ensemble Open-Meteo) | Afficher « J+5 : bon, mais incertain » au lieu d'un chiffre trompeur. | moyen |
| A7 | **Mise à jour de la forêt** : surfaces brûlées ou coupées depuis 2018 (masque forêt IGN 2021-2023, disponible sur la Géoplateforme ; ou Prométhée / EFFIS) | BD Forêt date de 2018. Une pinède brûlée y reste « idéale lactaires ». | moyen |

## B. Calibrer (dès 20 à 30 sorties)

| # | Piste | Pourquoi | Effort |
|---|---|---|---|
| B1 | **Ajuster les seuils et les poids** sur le journal, par une régression ordinale simple avec validation croisée | Remplacer les hypothèses par des valeurs mesurées. Attention au surapprentissage avec peu de sorties : on ne touche qu'à quelques paramètres. | moyen |
| B2 | **Archiver la prévision du jour** dans le rapport quotidien | Pour une sortie passée, la composante « prévision » utilise la pluie réellement tombée (léger avantage indu au modèle). | faible |
| B3 | **Saisie depuis le téléphone** : import d'un fichier GPX ou d'un point partagé | Le journal se remplit au PC, donc souvent de mémoire et avec des oublis, surtout pour les sorties bredouilles. | moyen |
| B4 | **Régler le bilan d'eau** (capacité, coefficient) avec les sorties, ou avec l'humidité du sol mesurée si une source ouverte locale existe | Ces deux valeurs sont des hypothèses. | moyen |

## C. Usage au quotidien

| # | Piste | Pourquoi | Effort |
|---|---|---|---|
| C2 | **Email seulement s'il y a du nouveau** : une zone passe en « bon » ou plus sur un créneau, ou forte variation depuis la veille | Un email par jour finit par ne plus être lu. | faible |
| C3 | **Lien vers une carte en ligne** depuis l'email (GitHub Pages) | La carte interactive est aujourd'hui locale. À ne faire **qu'avec le centre de Montpellier comme origine**, sinon le contour de la zone accessible révèle le domicile. | moyen |
| C4 | **Jours de battue** par département (calendriers des fédérations de chasse) | Le rapport rappelle seulement la période de chasse en général. | moyen, données à trouver |
| C5 | **Vigilance Météo-France automatique** | C'est aujourd'hui un simple rappel. L'API officielle demande une clé gratuite, à stocker en secret. | faible |

## D. Entretien

- **Actions GitHub** : mettre à jour les versions (avertissement Node 20). Les lancements programmés partent avec
  6 à 8 h de retard (en pratique vers 12-14 h au lieu de 6 h 30). C'est courant sur GitHub et sans conséquence
  pour un rapport du jour.
- **Suspension après 60 jours sans commit** sur un dépôt public : la réactivation se fait d'un clic. Un petit
  workflow mensuel peut aussi l'empêcher.
- **OSRM de démonstration** : service sans garantie. Le temps de trajet étant calculé une seule fois, le risque est
  faible.

---

## Ordre conseillé

1. Des sorties notées, y compris bredouilles : c'est la seule façon de savoir si le reste vaut l'effort.
2. **A2** (délai en degrés-jours) : la dernière simplification forte de la logique de délai.
3. **B2**, tôt, pour que l'évaluation future soit propre.
4. **C2** si les emails deviennent du bruit.
5. **B1** une fois 20 à 30 sorties réunies.
