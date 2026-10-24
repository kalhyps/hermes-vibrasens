"""Extrait des pages lues (pages/<id>.md) les passages utiles à chaque critère, avec l'adresse de leur page.

Pour chaque entreprise : son site, ses contacts et ses données de registre, puis, critère par critère, les
passages qui contiennent le plus de ses mots ; un nombre à côté d'un mot compte en plus (« 40 machines »,
« 70 salariés »). Les passages sont cités tels quels, prêts à servir de preuves. Un critère « adresse » est
ajouté d'office : les passages qui citent la ville ou le code postal de l'entreprise.

Exemples :
  python extract_passages.py --fiches enrichissement --critere "atelier=atelier,usine,site de production,m2" \
      --critere "machines=machines,parc,multibroches,centres d'usinage,tours" \
      --critere "effectif=salariés,collaborateurs,personnes,employés"
  python extract_passages.py --fiches qualification --base 2-enrichissement.csv --statut gardée --critere ...
  python extract_passages.py --critere ... --ids 316534569,605520527 --par-critere 5
Les mots sont cherchés sans accents ni majuscules, en début de mot : « recrut » trouve « recrutons ».
Avec --fiches <dossier>, l'outil donne les 10 entreprises suivantes qui n'ont pas encore de fiche dans ce dossier
(toutes vagues confondues, ou celles de --vague), et le nom du fichier où écrire leurs fiches : il suffit de le
relancer après chaque lot, jusqu'à « toutes les entreprises ont leur fiche ». Sans --fiches, --lot 1, --lot 2…
prend les entreprises par lots de 10 dans l'ordre du fichier de base.
"""
import argparse
import os
import re

from common import domain_of, fiche_ids, normalize, read_rows, root_domain, status_kind

SECTION_HEADER = re.compile(r"^## (\S+) : (\S+)\s*$")
SENTENCE_END = re.compile(r"(?<=[.!?;])\s+|\s+[–•|]\s+|\s+-\s+")
# Bandeaux et mentions sans intérêt pour juger une entreprise.
NOISE = ("cookie", "consent", "javascript", "navigateur", "browser", "confidentialit", "privacy", "rgpd", "gdpr",
         "cnil", "personnelles", "personnes physiques", "tous droits", "all rights", "copyright", "lire la suite")


def criterion(spec):
    """« machines=machines,parc,tours » -> ("machines", ["machines", "parc", "tours"])"""
    name, _, words = spec.partition("=")
    words = [normalize(w) for w in words.split(",") if normalize(w)]
    if not name.strip() or not words:
        raise SystemExit(f"Critère mal écrit : « {spec} ». Forme attendue : nom=mot1,mot2")
    return name.strip(), words


def place_words(row):
    """Ville (sans « cedex » ni arrondissement) et code postal de l'entreprise."""
    city = normalize(re.split(r"\d", row.get("ville") or "")[0]).replace(" cedex", "")
    return [w for w in (city, normalize(row.get("code_postal") or "")) if len(w) >= 3]


def passages(path):
    """(url de la page, passage, passage normalisé) pour chaque phrase utile, sans doublons ni bandeaux."""
    url, seen = "", set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            header = SECTION_HEADER.match(line)
            if header:
                url = header.group(2)
                continue
            if not url:
                continue  # titre du fichier et date de lecture
            for piece in SENTENCE_END.split(" ".join(line.split())):
                key = normalize(piece)
                if len(key) < 20 or key in seen or any(n in key for n in NOISE):
                    continue
                seen.add(key)
                yield url, piece, key


def score(key, words):
    """(points, premier mot trouvé) : un point par mot présent, deux de plus si un nombre l'accompagne."""
    found = [w for w in words if re.search(rf"\b{re.escape(w)}", key)]
    if not found:
        return 0, None
    points = len(found)
    if any(re.search(rf"\b\d+(?: \d{{3}})*(?: [a-z]+){{0,3}} {re.escape(w)}|\b{re.escape(w)}\w*(?: [a-z]+){{0,3}} \d", key)
           for w in found):
        points += 2
    return points, found[0]


def excerpt(text, word, length):
    """Le passage entier s'il est court, sinon une fenêtre autour du mot trouvé."""
    if len(text) <= length:
        return text
    # Même texte, caractère pour caractère, sans accents ni majuscules : pour situer le mot dans l'original.
    flat = "".join((normalize(c) or " ")[0] for c in text)
    found = re.search(r"\b" + r"\s+".join(re.escape(part) for part in word.split()), flat)
    start = max(0, min((found.start() if found else 0) - length // 3, len(text) - length))
    return ("…" if start else "") + text[start:start + length].strip() + ("…" if start + length < len(text) else "")


def best_passages(items, words, count, length):
    """Les `count` passages qui marquent le plus de points, à égalité dans l'ordre des pages."""
    scored = []
    for order, (url, text, key) in enumerate(items):
        points, word = score(key, words)
        if points:
            scored.append((-points, order, url, text, word))
    return [(url, excerpt(text, word, length)) for _, _, url, text, word in sorted(scored)[:count]]


def describe(row, site, reading):
    """En-tête d'une entreprise : identité, site, contacts, registre, avis de l'enrichisseur."""
    lines = [f"### {row['id']} · {row.get('nom', '')} · {row.get('code_postal', '')} {row.get('ville', '')}".rstrip()]
    if row.get("confiance_site") or not site:
        # 2-enrichissement.csv (site retenu par l'enrichisseur), ou entreprise pas encore cherchée
        url, confidence = row.get("site_web") or "", row.get("confiance_site") or ""
    else:
        # 1-collecte.csv : le site vérifié par find_website.py, pas celui que donnait la source
        url, confidence = site.get("site_web") or "", site.get("confiance") or ""
    if row.get("site_web") and not row.get("confiance_site") and (not url or root_domain(domain_of(row["site_web"]))
                                                                  != root_domain(domain_of(url))):
        lines.append(f"site donné par la source, non retenu : {row['site_web']}")
    parts = [f"site {url} (confiance {confidence})" if url else "aucun site"]
    if reading:
        parts.append(f"{reading.get('pages_lues') or 0} pages lues")
        parts += [reading[k] for k in ("emails_generiques", "telephones") if reading.get(k)]
        if reading.get("recrutement"):
            parts.append(f"recrutement : {reading['recrutement']}")
    lines.append(" · ".join(parts))
    registry = [f"{label} {row[key]}" for key, label in (
        ("id_registre", (row.get("registre") or "n°")), ("code_activite", "activité"), ("libelle_activite", "·"),
        ("effectif", "· effectif"), ("employeur", "· employeur :"), ("categorie", "· catégorie"),
        ("forme_juridique", "·"), ("date_creation", "· créée le"), ("ca", "· CA"), ("annee_ca", "en"),
        ("nb_etablissements", "· établissements :")) if row.get(key)]
    if row.get("effectif_etablissement"):
        kind = "siège" if row.get("siege") == "oui" else "établissement"
        registry.append(f"· {kind} de {row.get('ville', '')} : {row['effectif_etablissement']} salariés"
                        + (f" ({row['source_etablissement']})" if row.get("source_etablissement") else ""))
    if registry:
        lines.append("collecte : " + " ".join(registry))
    if row.get("indices"):
        lines.append(f"indices : {row['indices']}")
    if row.get("sources_collecte"):
        lines.append(f"trouvée dans : {row['sources_collecte']}" + (f" · vague {row['vague']}" if row.get("vague") else ""))
    if row.get("statut"):
        opinion = f"enrichissement : {row['statut']}"
        if row.get("effectif_site"):
            opinion += f" · effectif sur le site : {row['effectif_site']}"
        lines.append(opinion)
    if row.get("axes"):
        lines.append(f"axes d'approche (enrichisseur) : {row['axes']}")
    return lines


def next_batch_file(folder, batch):
    """Nom du prochain fichier de lot libre : <dossier>/vague-<n>-lot-<k>.json (ou lot-<k>.json sans vague)."""
    waves = sorted({r.get("vague") for r in batch if r.get("vague")})
    prefix = f"vague-{waves[0]}-lot-" if len(waves) == 1 else "lot-"
    number = 1
    while os.path.exists(os.path.join(folder, f"{prefix}{number}.json")):
        number += 1
    return os.path.join(folder, f"{prefix}{number}.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--critere", action="append", required=True,
                        help="nom=mot1,mot2 ; à répéter pour chaque critère du brief")
    parser.add_argument("--base", default="1-collecte.csv",
                        help="CSV des entreprises (1-collecte.csv, ou 2-enrichissement.csv pour la qualification)")
    parser.add_argument("--statut", help="seulement les entreprises de ce statut (ex. gardée)")
    parser.add_argument("--ids", help="identifiants à traiter, séparés par des virgules")
    parser.add_argument("--fiches", help="dossier des fiches de l'agent (enrichissement, qualification) : donne le lot "
                                         "suivant d'entreprises qui n'y ont pas encore de fiche")
    parser.add_argument("--vague", help="seulement les entreprises de cette vague (colonne vague)")
    parser.add_argument("--lot", type=int, default=1, help="sans --fiches : numéro du lot de 10 entreprises (1, 2…)")
    parser.add_argument("--taille-lot", type=int, default=10)
    parser.add_argument("--par-critere", type=int, default=3, help="passages gardés par critère")
    parser.add_argument("--longueur", type=int, default=260, help="longueur maximale d'un passage")
    parser.add_argument("--pages", default="pages", help="dossier des pages lues")
    args = parser.parse_args()

    criteria = [criterion(spec) for spec in args.critere]
    base = args.base
    rows = read_rows(base)
    total = len(rows)
    if args.statut:
        wanted_status = status_kind(args.statut) or args.statut
        rows = [r for r in rows if (status_kind(r.get("statut", "")) or r.get("statut")) == wanted_status]
    if args.vague:
        rows = [r for r in rows if r.get("vague") == args.vague]
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",")}
        rows = [r for r in rows if r["id"] in wanted]
    sites = {r["id"]: r for r in read_rows("sites.csv")} if os.path.exists("sites.csv") else {}
    readings = {r["id"]: r for r in read_rows("lecture.csv")} if os.path.exists("lecture.csv") else {}
    official = {}  # signaux-officiels.csv (signaux_officiels.py) : BODACC et RGE, datés, avec leur lien
    for fact in read_rows("signaux-officiels.csv") if os.path.exists("signaux-officiels.csv") else []:
        official.setdefault(fact["id"], []).append(fact)
    if not rows:
        filters = ", ".join(f"{k} {v}" for k, v in (("statut", args.statut), ("vague", args.vague), ("ids", args.ids)) if v)
        print(f"{base} : {total} entreprises, mais aucune ne correspond aux filtres ({filters}). Vérifie le statut, "
              "le numéro de vague (colonne vague) ou les identifiants.")
        return
    if args.fiches and not args.ids:
        done = fiche_ids(args.fiches)
        todo = [r for r in rows if r["id"] not in done]
        if not todo:
            print(f"{base} : {len(rows)} entreprises ; toutes les entreprises ont leur fiche dans {args.fiches}/.")
            return
        batch = todo[:args.taille_lot]
        target = next_batch_file(args.fiches, batch)
        print(f"{base} : {len(rows)} entreprises, {len(done & {r['id'] for r in rows})} déjà avec leur fiche, "
              f"{len(todo)} à faire ; voici les {len(batch)} suivantes.")
        print(f"Écris leurs fiches dans {target} (une liste JSON), puis relance cette commande.\n")
    else:
        batches = max(1, -(-len(rows) // args.taille_lot))
        batch = rows if args.ids else rows[(args.lot - 1) * args.taille_lot: args.lot * args.taille_lot]
        print(f"{base} : {len(rows)} entreprises" + ("" if args.ids else f", lot {args.lot} sur {batches}") + "\n")
    for row in batch:
        print("\n".join(describe(row, sites.get(row["id"], {}), readings.get(row["id"]))))
        for fact in official.get(row["id"], []):
            print(f"  {fact['type']} officiel{'le' if fact['type'] in ('alerte', 'certification') else ''} : "
                  f"{fact['date']} : {fact['fait']} ({fact['lien']}) [{fact['source']}]")
        path = os.path.join(args.pages, f"{row['id']}.md")
        if not os.path.exists(path):
            print("  pas de pages lues\n")
            continue
        items = list(passages(path))
        for name, words in criteria + [("adresse", place_words(row))]:
            found = best_passages(items, words, args.par_critere, args.longueur) if words else []
            print(f"  {name} :" + ("" if found else " rien dans les pages lues"))
            for url, text in found:
                print(f"    « {text} » ({url})")
        print()
    if args.fiches and not args.ids:
        rest = len(todo) - len(batch)
        print(f"Après ce lot : {rest} entreprise(s) sans fiche." if rest else "C'est le dernier lot.")
    elif not args.ids and args.lot < batches:
        print(f"Lot suivant : relancez avec --lot {args.lot + 1}.")


if __name__ == "__main__":
    main()
