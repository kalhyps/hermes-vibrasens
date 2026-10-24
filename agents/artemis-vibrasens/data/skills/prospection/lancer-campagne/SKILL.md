---
name: lancer-campagne
description: "Nouvelle campagne de prospection : cadrer avec l'utilisateur, écrire le brief, créer les tâches Kanban de la première vague."
version: 2.5.0
metadata:
  hermes:
    tags: [prospection, campagne, cadrage, kanban]
---

# Lancer une campagne de prospection

## 1. Cadrer avec l'utilisateur
Commence par lire le dossier client : `/workspace/client/offre.json` (ce qu'il vend, lu sur son site), `/workspace/client/icp.json` (la cible validée : critères, taille, activités, personas, déclencheurs, exclusions), `/workspace/client/references.csv` (ses vrais clients) et `/workspace/client/contexte.md`. S'il manque `offre.json` ou `icp.json`, ou si la ligne `prochaine_mise_a_jour` d'`offre.json` est passée, charge d'abord la skill `cadrage-client` et suis-la : la campagne attend que le dossier soit validé. Pars de ce que le dossier contient pour proposer la cible, au lieu de reposer des questions déjà réglées : les points ci-dessous qui sont dans `icp.json` sont acquis, et seuls ceux propres à la campagne restent à demander (l'offre visée s'il en a plusieurs, la zone, l'objectif, LinkedIn, les sources payantes, le budget).

**Définis la cible avec le client, puis attends son accord avant de construire une liste.** La cible tient en quatre points, à valider ensemble : le secteur, la taille d'entreprise, le poste visé (la fonction du décideur à approcher, sans nom de personne) et les problèmes que le client sait résoudre chez ces entreprises. Aucune tâche Kanban n'est créée sans cet accord.

Pose les questions de cadrage sur ce qui manque, puis présente le brief en quelques lignes, en distinguant ce que l'utilisateur a dit de ce que tu proposes ou déduis. Une campagne vise **une offre à la fois** : si `offre.json` en liste plusieurs, demande laquelle, et le brief la nomme. **Attends sa validation explicite avant d'écrire `brief.md` et de créer les tâches**, même quand son premier message contient déjà tout : une campagne lancée sur un malentendu coûte une heure de travail de l'équipe et des crédits. Les points à couvrir :
- **l'offre** de la campagne : laquelle des offres d'`offre.json`, à qui, et le problème qu'elle résout (recopie, ne redemande pas) ;
- **la cible** : secteur ou métier, type d'organisation, en mots simples. Les codes d'activité, c'est le collecteur qui les trouve ;
- **le poste visé** : la fonction du décideur que le client veut approcher (directeur industriel, responsable maintenance, DAF…). Il oriente l'accroche ; l'équipe ne cherche aucun nom de personne ;
- **les problèmes que le client sait résoudre** : ceux que ses clients actuels avaient, et ce qui les révèle chez un prospect ;
- **les modèles** : les clients actuels qui représentent le mieux la cible, et ce qu'ils ont en commun ;
- **la zone** : le ou les pays, puis les régions, provinces, États ou villes, sous leur nom local (« Liège », « West Midlands », « Bayern », « Ohio »). Tous les pays sont possibles. Reprends les `zones` d'`icp.json` avec leurs codes (par exemple « Métropole européenne de Lille, EPCI 200093201 ») ;
- **la taille** : effectif minimal, chiffre d'affaires. Si l'utilisateur ne fixe rien, propose un minimum cohérent avec l'offre, explique pourquoi, et fais-le valider ;
- **les exclusions** : clients actuels, concurrents, secteurs (reprends ceux du contexte client) ;
- **les signaux récents** recherchés : toujours les recrutements, les changements d'outil (nouveau logiciel, nouvelle machine, migration) et les lancements d'offre, et, en France, ceux que les registres officiels datent d'eux-mêmes (ouverture d'établissement, changement de direction, déménagement, qualification RGE obtenue : BODACC et annuaire RGE, lus par l'équipe sans coût), plus ceux propres à l'offre (croissance, investissements, marchés publics…), et la période qui les rend « récents » (propose-la d'après le cycle de vente du client et fais-la valider) ;
- **les axes d'approche** : ceux de l'offre visée, dans `offre.json` (`axes`), **pour la cible de la campagne** (`cible`). Si un jeu d'axes a été validé pour cette cible, recopie-le sans le redemander. Si l'offre n'a des axes que pour une autre cible (les artisans du bâtiment, quand la campagne vise des intégrateurs industriels), adapte-les : garde ceux dont les signes se liront aussi sur les sites de la nouvelle cible, remplace les autres par des problèmes propres à son métier (pour un intégrateur : chiffrage d'une affaire en plusieurs postes, versions successives d'une offre, relevé ou audit sur site, réemploi des offres d'affaires voisines, délai de réponse aux consultations), avec les mêmes rubriques. Présente ce jeu adapté, dis ce qui change par rapport au premier, et fais-le valider avant le brief complet ; écris-le ensuite dans `offre.json` avec sa `cible`, à côté du premier. Un axe enregistré sans `cible` vaut pour la cible de la campagne où il a été validé (`contexte.md`, `brief.md` de cette campagne) : complète alors sa `cible`. S'il n'y a aucun axe, propose-en trois à cinq comme le dit la skill `cadrage-client` (étape 1). Sans axes adaptés à la cible, l'équipe écrit des accroches justes sur la forme mais à côté du métier ;
- **ce qu'est un bon lead** : des critères vérifiables, les critères éliminatoires, et ceux qui comptent le plus. Un critère indispensable est un **fait observable** sur un site ou dans un registre (le métier, la zone, la taille, une installation chez les clients), jamais un fonctionnement interne qui va de soi pour ce métier : un installateur sur mesure travaille forcément sur devis, un transporteur a des chauffeurs. Ce qui va de soi n'est pas un critère ; ce qui le rend plus probable ou plus fort (un formulaire de devis, des relevés sur site) va dans les critères appréciés ;
- **l'objectif** : le nombre de leads qualifiés visé, ou « tous les leads de la zone » (exhaustivité) ;
- **LinkedIn** : l'utiliser comme source ou non. À la collecte, le collecteur n'y prend que des pages d'entreprises, sans jamais se connecter. Les décideurs publics (la fonction visée par le brief) ne sont cherchés qu'après la qualification, pour les seuls leads qualifiés ; le client les valide avec son propre compte, puis Dropcontact cherche l'email des seuls contacts validés (un crédit par contact). Propose-le quand le client veut des personnes à contacter, pas seulement des entreprises. Les clés Serper et Dropcontact sont les siennes : la ligne « Sources payantes » ne les concerne pas. Indépendamment de LinkedIn, pour les leads qualifiés français de petite taille, le dirigeant publié au registre est proposé au client comme décideur à valider (skill `bilan-campagne`, 3 ter) : le brief le dit (« Dirigeants du registre : oui, jusqu'à <borne haute de la taille> salariés », ou « non » si le client refuse) ;
- **les sources payantes** : autorisées ou non (non par défaut). Les sources gratuites et Firecrawl sont toujours permises ;
- **le budget Firecrawl** (facultatif) : le nombre de crédits que la campagne peut dépenser. Firecrawl (payant) ne sert qu'aux sites que les moteurs gratuits ne trouvent pas et aux pages qui exigent un navigateur ; sans budget, il sert à chaque fois que c'est utile. Si l'utilisateur veut maîtriser la dépense, propose un budget d'après la taille de la campagne et fais-le valider.

### La validation du brief
Elle se fait en deux temps, et le second ne se saute jamais :
1. **La cible** : secteur, taille, poste visé, problèmes résolus. Le client la valide.
2. **Le brief complet** : quand chaque point ci-dessus est réglé, dit par le client ou proposé par toi, envoie-lui le brief en une liste numérotée, un point par ligne, en marquant « (proposé) » ce qu'il n'a pas dit lui-même : offre, cible, poste visé, problèmes, axes d'approche, modèles, zone, taille, exclusions, signaux et leur période, critères indispensables et appréciés, objectif, LinkedIn oui ou non, sources payantes, budget Firecrawl. Termine par une seule question : « Je lance la campagne sur ce brief ? ». Seule la réponse à cette question vaut validation.

Un « allez-y », « lancez », « c'est bon pour moi » reçu avant que tu aies envoyé ce brief complet ne vaut pas validation, même s'il accompagne l'accord sur la cible : réponds en envoyant le brief, puis attends. Un client pressé perd moins de temps à relire quinze lignes qu'à refaire une campagne.

## 2. Écrire le brief
Écris le brief validé dans `/workspace/campagnes/<campagne>/brief.md`, où `<campagne>` est un nom court, sans espace ni accent. Il contient :
- une section **Dossier client**, recopiée d'`offre.json` et d'`icp.json` pour ce qui sert à la campagne : site du client, l'offre visée et le problème qu'elle résout, les différenciateurs prouvés (avec leur preuve), les affirmations à ne jamais faire, les concurrents, les clients actuels (exclusions et modèles : `références : /workspace/client/references.csv`), les deux personas (rôle économique et rôle technique, intitulés par langue) ;
- une ligne `Offre : <nom de l'offre dans offre.json>` ;
- la cible, le poste visé, la zone (pays et zones sous leur nom local, avec leurs codes de registre s'ils sont dans `icp.json`), la taille (celle d'`icp.json` avec son origine, sauf si le client la change pour cette campagne), les exclusions ;
- **Signaux récents** : les signaux recherchés et leur période (« depuis <date> »), avec la règle : chaque signal est noté avec sa date et le lien de sa source ;
- **Axes d'approche** : un axe par ligne, `<nom> : <problème> ; signes : <mots qu'on lit sur un site> ; l'offre change : <…> ; ne pas promettre : <…>`. L'enrichisseur cherche leurs signes, le qualificateur choisit pour chaque lead l'axe que ses faits appuient le mieux. Les critères appréciés viennent en bonne partie de ces signes ;
- les critères d'un bon lead, en deux listes : **indispensables** (sans eux, l'entreprise n'est pas un lead ; le qualificateur les vérifie un par un) et **appréciés** (ils classent les leads), avec leur importance relative ;
- **Objectif** : `<N> leads qualifiés`, ou `exhaustivité` ;
- **Taille de la première vague** : seulement si l'utilisateur en fixe une ; sinon, écris que le collecteur la calcule ;
- **Sources payantes** : autorisées (lesquelles) ou non ;
- **LinkedIn** : `LinkedIn : oui` ou `LinkedIn : non`, sur une ligne à part (le collecteur lit cette ligne telle quelle) ;
- **Budget Firecrawl** : seulement si l'utilisateur en fixe un, sur une ligne de la forme `Budget Firecrawl : <N> crédits` (les outils la lisent telle quelle et cessent d'utiliser Firecrawl au-delà).

Mets aussi à jour `/workspace/client/contexte.md` (rubrique « Cibles validées ») avec la cible que le client vient d'approuver et la date. Si le client a changé un critère, une taille ou une exclusion pour cette campagne, et que ce changement vaut pour les suivantes, reporte-le dans `icp.json` avec la date et « dit par le client ».

Ne fixe pas toi-même de volume de collecte : le collecteur cherche toutes les entreprises de la cible, puis envoie à l'analyse des vagues dimensionnées d'après l'objectif. N'impose ni statuts ni méthode aux étapes : chaque agent a les siens.

## 3. Créer les tâches de la vague 1
Crée quatre tâches enchaînées avec `kanban_create`, toutes avec l'espace de travail `dir` et le chemin `/workspace/campagnes/<campagne>` :
1. « Collecte — <campagne> », pour `collecteur` ;
2. « Enrichissement — <campagne> — vague 1 », pour `enrichisseur`, avec la collecte pour parent ;
3. « Qualification — <campagne> — vague 1 », pour `qualificateur`, avec l'enrichissement pour parent ;
4. « Bilan — <campagne> — vague 1 », pour `default` (toi : ton profil Hermes s'appelle `default`), avec la qualification pour parent et la skill `bilan-campagne`.

Contenu du corps de chaque tâche :
- **Toutes** : le résumé du brief, les fichiers à lire et à produire, et une ligne `Vague : 1` (sauf la collecte).
- **Collecte** : l'objectif, la taille de la première vague si le brief la fixe, le fait que les sources payantes sont autorisées ou non, la ligne `LinkedIn : oui` ou `LinkedIn : non` du brief, et le rappel que la cible détaillée est dans `/workspace/client/icp.json` et les clients de référence dans `/workspace/client/references.csv`.
- **Enrichissement et qualification** : rien de plus que le résumé du brief et `Vague : 1` ; leur méthode fixe déjà les fichiers à lire et à produire.

Ne joins pas de skill aux trois premières tâches : chaque agent charge la sienne d'office.

## 4. Prévenir l'utilisateur
Dis à l'utilisateur que la campagne est lancée et comment la suivre : en vous demandant où elle en est (tu regardes alors ses tâches Kanban et `vagues.json`). Précise que d'autres vagues seront lancées d'elles-mêmes tant que l'objectif n'est pas atteint et que la réserve n'est pas épuisée. Si LinkedIn est retenu, préviens-le qu'il recevra, après la qualification de chaque vague, les décideurs des leads qualifiés à valider avant tout enrichissement.
