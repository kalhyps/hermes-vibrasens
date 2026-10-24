# Types de sources d'entreprises

À passer en revue pour chaque nouveau pays ou secteur. Les exemples servent à reconnaître un type de source ; ils ne remplacent pas la recherche, car les sources changent. Vérifie toujours par un essai réel.

## 1. Registres officiels des entreprises
- **Ce qu'ils apportent** : la liste la plus complète des entreprises enregistrées, avec numéro officiel, forme juridique, adresse du siège, souvent le code d'activité, parfois l'effectif ou les comptes.
- **Limites** :
  - le code d'activité est déclaratif et parfois ancien ;
  - l'effectif est souvent absent ;
  - l'adresse est souvent celle du siège, pas de l'atelier.
- **Niveau** :
  - national dans la plupart des pays ;
  - par État ou province dans les pays fédéraux. Aux États-Unis, chaque État tient son registre (Secretary of State) : couverture inégale, recherche souvent par nom seulement, extractions en masse parfois payantes.
- **Accès** : API, fichier complet téléchargeable (souvent mensuel), ou recherche en ligne seulement. Un fichier complet est souvent plus simple qu'une API à quotas.
- **Exemples** :
  - France : API Recherche d'entreprises, stock SIRENE ;
  - Royaume-Uni : Companies House, API et fichier mensuel ;
  - Norvège : Brønnøysundregistrene, API ouverte avec code NACE et effectif ;
  - Belgique : BCE/KBO, fichier ouvert avec inscription ;
  - Suisse : Zefix.
- **Comment chercher** : « <pays> business register API », « <pays> company register bulk data », nom local du registre.

## 2. Registres statistiques et données ouvertes publiques
- **Ce qu'ils apportent** : instituts statistiques et portails de données ouvertes. Fichiers d'établissements avec activité et tranche d'effectif dans certains pays ; ailleurs, seulement des agrégats.
- **Limites** : les agrégats (nombre d'entreprises par code et zone, comme le County Business Patterns américain) ne listent pas d'entreprises. Ils servent à **dimensionner** : combien d'entreprises la cible compte-t-elle dans la zone ? C'est utile pour le contrôle de rappel.
- **Comment chercher** : « <pays> open data portal », « data.<domaine du pays> companies », « <institut statistique> establishments dataset ».

## 3. Registres réglementaires et sectoriels
- **Ce qu'ils apportent** : des listes d'établissements qu'une autorité suit, souvent avec adresse, activité et parfois effectif. Excellent pour l'industrie :
  - installations classées ou permis environnementaux (États-Unis : Facility Registry Service et ECHO de l'EPA, avec codes NAICS ; Union européenne : registre des émissions industrielles) ;
  - établissements de santé, d'alimentation ou de dispositifs médicaux enregistrés ;
  - organismes agréés (aéronautique, transport, énergie), certifications publiques.
- **Limites** : seulement les établissements soumis à la réglementation.
- **Comment chercher** : « <secteur> establishments registry <pays> », « <autorité> facility data download », « <pays> industrial facilities database ».

## 4. Marchés publics et fournisseurs
- **Ce qu'ils apportent** : les fournisseurs enregistrés ou attributaires de marchés, avec leur activité. Exemples :
  - SAM.gov aux États-Unis (entités enregistrées, codes NAICS ; API avec clé gratuite) ;
  - TED pour l'Union européenne ;
  - les plateformes nationales d'avis de marchés.
- **Limites** : seulement les entreprises qui travaillent avec le secteur public. C'est aussi un signal d'affaires.

## 5. Bases de lieux
- **Ce qu'elles apportent** : des lieux géolocalisés dans tous les pays, souvent avec site et téléphone :
  - **Overture Maps** : connecteur prêt, `connectors/overture.py` ;
  - **OpenStreetMap** : API Overpass, étiquettes `craft=*`, `industrial=*`, `man_made=works`, `office=company` ; licence ODbL, qui impose de citer la source ;
  - **Foursquare Open Source Places**.
- **Limites** :
  - ni effectif ni numéro officiel ;
  - catégories approximatives ;
  - inégales selon les pays et les secteurs : les commerces y sont mieux couverts que les ateliers.

## 6. Organisations professionnelles
- **Ce qu'elles apportent** : fédérations et syndicats de métier, chambres de commerce et d'industrie, pôles et clusters régionaux, associations d'exportateurs. Leurs annuaires de membres ciblent exactement un métier, souvent avec site et contact.
- **Limites** :
  - seulement les membres ;
  - pages parfois chargées par script (navigateur nécessaire) ;
  - conditions d'utilisation à lire.
- **Comment chercher** : « <métier dans la langue du pays> association », « <métier> federation members directory », « <secteur> cluster <région> ».

## 7. Salons et événements
- **Ce qu'ils apportent** : les listes d'exposants des salons du secteur, par édition. Les exposants sont actifs et investissent dans la vente : c'est aussi un signal.
- **Limites** : distributeurs et étrangers mêlés aux fabricants ; liste parfois en PDF ou dans une application.
- **Comment chercher** : « <métier> trade show <pays> », « <salon> exhibitor list <année> ».

## 8. Classements, presse et listes publiques
- **Ce qu'ils apportent** : classements régionaux, palmarès, presse économique, listes de lauréats de programmes publics. C'est surtout utile pour le **contrôle de rappel** : les acteurs connus de la cible doivent être dans le vivier.

## 9. Identifiants transverses
- **GLEIF** (identifiant LEI, API gratuite) : surtout les moyennes et grandes entreprises, avec leurs liens de groupe.
- **Wikidata** (SPARQL) : surtout les grandes entreprises, avec leur site officiel.
- **OpenCorporates** : agrège de nombreux registres ; l'accès gratuit est limité, vérifie ses conditions.
- **Ce qu'ils apportent** : relier des sources, et identifier les groupes.

## 10. Annuaires commerciaux
- Exemples : Kompass, Europages, Thomasnet, pages jaunes.
- **Ce qu'ils apportent** : une bonne couverture des fournisseurs industriels.
- **Limites** : leurs conditions interdisent en général l'extraction en masse. Utilise-les seulement pour vérifier à la main quelques noms (contrôle de rappel), sauf autorisation explicite dans le brief.

## À écarter
- Sources centrées sur des personnes : réseaux sociaux professionnels, annuaires de dirigeants.
- Sources payantes sans accord dans le brief.
- Pages derrière un identifiant, contenus protégés par captcha.
- Listes achetées ou « fuites » de données.
