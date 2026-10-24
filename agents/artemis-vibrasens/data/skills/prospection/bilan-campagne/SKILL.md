---
name: bilan-campagne
description: "Tâche « Bilan » d'une vague : faire le point (vagues.json, fichiers de l'équipe), décider de lancer la vague suivante ou d'arrêter, et écrire bilan.md."
version: 2.2.0
metadata:
  hermes:
    tags: [prospection, campagne, bilan, vagues]
---

# Bilan d'une vague

Tu travailles seul, dans une tâche Kanban : ne pose pas de question.

## 1. Faire le point
Lis, dans ton dossier de travail :
- `brief.md` : l'objectif (nombre de leads qualifiés, ou exhaustivité) ;
- `vagues.json` : pour chaque vague, les entreprises envoyées, gardées, qualifiées et à revoir, et le rendement (qualifiées / envoyées) ; la réserve du vivier ; le total. Si la vague de cette tâche n'a pas encore de résultats de qualification (`qualification_faite` à `false`), prends les chiffres du résumé de la tâche de qualification et signale l'écart dans les difficultés ;
- le livrable `leads.csv`, `3-qualification.csv`, et les résumés des tâches de la vague (collecte ou vague, enrichissement, qualification) ;
- `firecrawl.json` s'il existe : les crédits Firecrawl dépensés par la campagne, par outil, et le budget du brief.

## 2. Décider
Prends la première règle qui s'applique :
1. **Objectif atteint** (qualifiées ≥ objectif) : la campagne est terminée.
2. **Exhaustivité demandée** : la campagne est terminée quand la réserve est vide. Sinon, lance une vague avec toute la réserve.
3. **Rendement nul** : la dernière vague n'a donné aucune entreprise qualifiée. La campagne s'arrête, car descendre plus bas dans un classement qui n'a rien donné serait du temps perdu. Explique ce qu'il faudrait revoir : critères trop durs, cible mal traduite, sources inadaptées, sites introuvables. Si des entreprises sont « à revoir » parce qu'un même critère reste invérifiable (par exemple l'effectif, quand aucune source du pays ne le publie), dis-le avec leur nombre : ce sont des leads à confirmer par le commercial, présents dans le livrable, et le critère est à rediscuter avec l'utilisateur ou à prouver par une autre source.
4. **Réserve vide** (`vivier.reserve` à 0), **ou réserve vérifiable vide** (`vivier.reserve_exploitable` à 0 : il ne reste que des entreprises sans site ni effectif connus, que l'enrichissement écarte presque toutes) : la campagne s'arrête. Recommande de quoi élargir (sources qui donnent un site ou un effectif, zones voisines, tailles ou codes voisins), avec ce que chaque piste apporterait ; c'est à l'utilisateur de décider.
5. **Sinon, lance la vague suivante.**
   - Taille : (objectif − qualifiées) ÷ rendement cumulé (total des qualifiées ÷ total des envoyées), arrondi au-dessus.
   - Si la réserve est plus petite, le collecteur élargira le vivier ou enverra ce qu'il reste.

## 3. Lancer la vague suivante (règle 5, ou règle 2 avec réserve non vide)
Crée quatre tâches avec `kanban_create`, toutes avec l'espace de travail `dir` et le chemin de la campagne. `<n>` est le numéro de la nouvelle vague.
1. « Vague <n> — <campagne> », pour `collecteur`, avec ta tâche en cours pour parent (son identifiant est dans `kanban_show`). Le corps contient : `Vague : <n>`, la taille et son calcul, l'objectif, le nombre de qualifiées obtenues, et le rendement de chaque vague.
2. « Enrichissement — <campagne> — vague <n> », pour `enrichisseur`, avec la tâche de vague pour parent ; le corps contient `Vague : <n>`.
3. « Qualification — <campagne> — vague <n> », pour `qualificateur`, avec l'enrichissement pour parent ; le corps contient `Vague : <n>`.
4. « Bilan — <campagne> — vague <n> », pour `default` (toi : ton profil Hermes s'appelle `default`), avec la qualification pour parent, et la skill `bilan-campagne`.

## 3 bis. Décideurs LinkedIn (si le brief dit `LinkedIn : oui`)
Si la vague a donné des leads `qualifié`, crée une tâche « Décideurs LinkedIn — <campagne> — vague <n> » pour `collecteur`, espace de travail `dir` et chemin de la campagne, avec ta tâche en cours pour parent. Son corps : `Vague : <n>`, le poste visé par le brief (c'est l'intitulé cherché), et « décideurs publics des leads qualifiés de la vague <n> (skill `linkedin-prospection`, étapes 3 et 4), puis liens de validation ». Cette tâche se crée quelle que soit la décision de la section 2 : elle ne cherche des personnes que chez les entreprises déjà jugées intéressantes, jamais dans le vivier. Sans lead qualifié dans la vague, pas de tâche.

## 3 ter. Dirigeants du registre (petites entreprises françaises)
Dans une entreprise de quelques dizaines de salariés, le décideur est le dirigeant, et le registre français le publie. Si la vague a donné des leads `qualifié` en France, lance toi-même, depuis le dossier de la campagne :
`/opt/leadgen/venv/bin/python /opt/data/leadgen/outils/dirigeants_registre.py --max-effectif <borne haute de la taille du brief, 49 au plus>`
Il ajoute les dirigeants personnes physiques des leads qualifiés dont l'effectif tient dans la borne à `linkedin/contacts.csv`, au statut `a_valider`, avec la fiche du registre pour source (ni date de naissance ni nationalité). Présente-les ensuite au client, une ligne par dirigeant (entreprise, nom, qualité), et demande-lui lesquels il valide : seuls ceux qu'il valide passent au statut `valide`, entrent dans `leads.xlsx` et `decideurs.xlsx` (à l'export suivant) et peuvent être enrichis par Dropcontact (un crédit chacun, avec son accord). Rappelle-lui que le premier message à un dirigeant doit dire d'où vient son nom (registre public) et qu'il peut s'opposer à tout nouveau contact (RGPD). Le brief peut l'écarter (« Dirigeants du registre : non ») ; sinon c'est la règle par défaut, que le brief dise `LinkedIn : oui` ou non.

## 4. Écrire `bilan.md`
Réécris-le en entier, cumulé sur toutes les vagues :
- **État** : campagne terminée (objectif atteint ou exhaustivité), arrêtée (avec la raison), ou vague <n> lancée (avec sa taille et son calcul) ;
- **Livrable** : `leads.xlsx` (et `leads.csv`), avec son nombre de leads qualifiés et à revoir. S'il manque, dis-le dans les difficultés. Si une tâche « Décideurs LinkedIn » a été créée, dis-le : le client recevra les décideurs des leads qualifiés à valider, puis `decideurs.xlsx` avec `leads.xlsx` une fois les contacts validés enrichis ;
- **Chiffres par vague** : envoyées, gardées, qualifiées, à revoir, rendement ; puis le vivier (entreprises, exclues, réserve) ;
- **Coût** : crédits Firecrawl dépensés (`firecrawl.json`), et le budget s'il y en a un ;
- **Les 10 meilleurs leads**, avec leur score et la raison principale ;
- **Les cas à revoir** ;
- **Les difficultés rencontrées**, et ce qui pourrait améliorer la campagne suivante.

## 5. Terminer
Termine la tâche avec `kanban_complete`. Le résumé reprend l'état et les chiffres clés.
