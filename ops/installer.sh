#!/bin/bash
# Réglages d'artemis-vibrasens sur le serveur : Google Chat et clés au nom de Vibrasens.
# Lancé par Nico, avec sudo, depuis le dépôt cloné sur le serveur :
#
#   sudo bash /srv/hermes/clients/vibrasens/depot/ops/installer.sh google-chat <clé.json> <adresse> [client-oauth.json]
#       range la clé du compte de service Google dans data/google-chat/credentials.json et
#       écrit les réglages GOOGLE_CHAT_* dans data/.env (projet lu dans la clé, abonnement
#       vibrasens-chat-sub), avec <adresse> comme seul utilisateur autorisé. Le client OAuth
#       facultatif sert aux pièces jointes (leads.xlsx). Les fichiers d'origine sont supprimés.
#
#   sudo bash /srv/hermes/clients/vibrasens/depot/ops/installer.sh cle <NOM>
#       demande la valeur de la clé <NOM> (rien ne s'affiche pendant la saisie) et l'écrit dans
#       le .env des quatre profils, à la place de l'ancienne. Affiche seulement sa longueur.
#
#   sudo bash /srv/hermes/clients/vibrasens/depot/ops/installer.sh cles
#       dit, profil par profil, quelles clés sont posées et lesquelles sont encore celles de
#       Nicolas (copiées d'Artémis le 24/10), sans afficher aucune valeur.
#
#   sudo bash /srv/hermes/clients/vibrasens/depot/ops/installer.sh retirer-cles-de-nicolas
#       efface des .env d'artemis-vibrasens toutes les clés encore identiques à celles d'Artémis.
#
# Puis : sudo -u hermes hermes-ctl redemarrer artemis-vibrasens
set -euo pipefail

CIBLE=/srv/hermes/clients/vibrasens/agents/artemis-vibrasens/data
ARTEMIS=/srv/hermes/agents/artemis/data
ABONNEMENT=vibrasens-chat-sub
PROFILS=(default collecteur enrichisseur qualificateur)
# Clés que les agents savent utiliser (data/.env.example).
CLES=(TINYFISH_API_KEY KEENABLE_API_KEY PERPLEXITY_API_KEY FIRECRAWL_API_KEY
      LEADGEN_FIRECRAWL_API_KEY COMPANIES_HOUSE_API_KEY SERPER_API_KEY DROPCONTACT_API_KEY)

[ "$(id -u)" = 0 ] || { echo "À lancer avec sudo." >&2; exit 2; }
[ -d "$CIBLE" ] || { echo "artemis-vibrasens n'est pas installé ($CIBLE absent)." >&2; exit 2; }

env_de() { if [ "$1" = default ]; then echo "$CIBLE/.env"; else echo "$CIBLE/profiles/$1/.env"; fi; }
env_artemis() { if [ "$1" = default ]; then echo "$ARTEMIS/.env"; else echo "$ARTEMIS/profiles/$1/.env"; fi; }
valeur() { if [ -f "$1" ]; then grep -m1 "^$2=" "$1" | cut -d= -f2- || true; fi; }
# Vrai si <valeur> est celle de <NOM> dans l'un des profils d'Artémis (les clés de Nicolas).
de_nicolas() {
  local p
  for p in "${PROFILS[@]}"; do [ "$(valeur "$(env_artemis "$p")" "$1")" = "$2" ] && return 0; done
  return 1
}

# Écrit un fichier lisible par hermes seul.
ecrire() { install -o hermes -g hermes -m 600 "$1" "$2"; }

# Remplace (ou ajoute) VAR=valeur dans un .env, sans toucher aux autres lignes ; valeur vide = retirer.
regler() {
  local fichier="$1" var="$2" val="$3" tmp
  tmp=$(mktemp)
  if [ -f "$fichier" ]; then grep -v "^$var=" "$fichier" > "$tmp" || true; fi
  [ -z "$val" ] || printf '%s=%s\n' "$var" "$val" >> "$tmp"
  [ -d "$(dirname "$fichier")" ] || install -d -o hermes -g hermes -m 700 "$(dirname "$fichier")"
  ecrire "$tmp" "$fichier"; rm -f "$tmp"
}

google_chat() {
  local cle="${1:-}" adresse="${2:-}" client="${3:-}" projet
  [ -f "$cle" ] || { echo "Clé du compte de service introuvable : « $cle »." >&2; exit 2; }
  [[ "$adresse" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[a-z]{2,}$ ]] || { echo "Adresse invalide : « $adresse »." >&2; exit 2; }
  projet=$(python3 -I -c '
import json, sys
d = json.load(open(sys.argv[1]))
assert d.get("type") == "service_account"
print(d["project_id"])' "$cle") || { echo "Le fichier n'est pas une clé de compte de service Google." >&2; exit 2; }
  install -d -o hermes -g hermes -m 700 "$CIBLE/google-chat"
  ecrire "$cle" "$CIBLE/google-chat/credentials.json"
  echo "Clé du compte de service rangée ($(stat -c %s "$CIBLE/google-chat/credentials.json") octets), projet $projet."
  regler "$CIBLE/.env" GOOGLE_CHAT_PROJECT_ID "$projet"
  regler "$CIBLE/.env" GOOGLE_CHAT_SUBSCRIPTION_NAME "projects/$projet/subscriptions/$ABONNEMENT"
  regler "$CIBLE/.env" GOOGLE_CHAT_SERVICE_ACCOUNT_JSON /opt/data/google-chat/credentials.json
  regler "$CIBLE/.env" GOOGLE_CHAT_ALLOWED_USERS "$adresse"
  echo "Réglages Google Chat écrits dans data/.env, seul autorisé : $adresse."
  if [ -n "$client" ]; then
    [ -f "$client" ] || { echo "Client OAuth introuvable : « $client »." >&2; exit 2; }
    python3 -I -c '
import json, sys
assert "installed" in json.load(open(sys.argv[1]))' "$client" \
      || { echo "Le client OAuth doit être de type « Application de bureau » (Desktop app)." >&2; exit 2; }
    ecrire "$client" "$CIBLE/google_chat_user_client_secret.json"
    echo "Client OAuth des pièces jointes rangé."
    rm -f -- "$client"
  fi
  rm -f -- "$cle"
  echo "Fichiers d'origine supprimés. Ensuite : sudo -u hermes hermes-ctl redemarrer artemis-vibrasens"
}

cle() {
  local nom="${1:-}" val p
  [[ " ${CLES[*]} " == *" $nom "* ]] || { echo "Clé inconnue : « $nom ». Clés possibles : ${CLES[*]}" >&2; exit 2; }
  read -r -s -p "Colle la valeur de $nom puis appuie sur Entrée (rien ne s'affiche) : " val; echo
  val=$(printf '%s' "$val" | tr -d '[:space:]')
  [ -n "$val" ] || { echo "Valeur vide : rien n'a changé." >&2; exit 2; }
  for p in "${PROFILS[@]}"; do regler "$(env_de "$p")" "$nom" "$val"; done
  echo "$nom posée dans les ${#PROFILS[@]} profils (${#val} caractères)."
  echo "Ensuite : sudo -u hermes hermes-ctl redemarrer artemis-vibrasens"
}

cles() {
  local p n v ligne
  for p in "${PROFILS[@]}"; do
    ligne="$p :"
    for n in "${CLES[@]}"; do
      v=$(valeur "$(env_de "$p")" "$n")
      if [ -z "$v" ]; then continue
      elif de_nicolas "$n" "$v"; then ligne+=" $n (celle de Nicolas)"
      else ligne+=" $n (Vibrasens)"; fi
    done
    echo "$ligne"
  done
}

retirer() {
  local p n v k=0
  for p in "${PROFILS[@]}"; do
    for n in "${CLES[@]}"; do
      v=$(valeur "$(env_de "$p")" "$n")
      if [ -n "$v" ] && de_nicolas "$n" "$v"; then regler "$(env_de "$p")" "$n" ""; echo "$p : $n retirée"; k=$((k + 1)); fi
    done
  done
  echo "$k clé(s) de Nicolas retirée(s). Ensuite : sudo -u hermes hermes-ctl redemarrer artemis-vibrasens"
}

case "${1:-}" in
  google-chat) shift; google_chat "$@" ;;
  cle) shift; cle "$@" ;;
  cles) cles ;;
  retirer-cles-de-nicolas) retirer ;;
  *) sed -n '2,23p' "$0"; exit 2 ;;
esac
