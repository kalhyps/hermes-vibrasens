"""Vagues d'une campagne : envoie les entreprises du vivier en enrichissement par vagues, et fait le point.

  --taille N   ajoute une vague à 1-collecte.csv : les N premières entreprises candidates (non exclues) de la réserve,
               dans l'ordre de priorité du vivier, parmi celles qu'on peut vérifier : un site connu ou un effectif au
               registre. Les autres (ni site ni effectif) ne partent qu'avec --inexploitables : le 20/10, une vague
               de 82 entreprises dont 62 sans site vérifiable n'a donné aucun lead. --ids ajoute des entreprises précises.
  --etat       fait le point sans rien envoyer.
Dans les deux cas, l'outil écrit vagues.json : pour chaque vague, entreprises envoyées, gardées par l'enrichissement,
qualifiées, à revoir, écartées (quand ces étapes ont eu lieu), et le rendement (qualifiées / envoyées) ; la taille de
la réserve ; l'objectif de la campagne s'il a été donné (--objectif, gardé ensuite).

La taille d'une vague n'a pas de valeur par défaut : elle se calcule (skill collecte-entreprises, étape « Vague »).

Exemples :
  python waves.py --taille 60 --objectif 15
  python waves.py --etat
"""
import argparse
import csv
import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import normalize, read_rows  # noqa: E402
from connector_base import read_json, write_json  # noqa: E402

csv.field_size_limit(10_000_000)
SENT = "1-collecte.csv"


def write_csv(path, rows, fields):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def status_by_id(path, column):
    return {r["id"]: normalize(r.get(column, "")) for r in read_rows(path)} if os.path.exists(path) else {}


def status_kind(status):
    """« qualifié », « Qualifiée », « à revoir », « écarté(e) », « gardée »… -> qualifie, a revoir, ecarte, garde."""
    for prefix, kind in (("qualifi", "qualifie"), ("a revoir", "a revoir"), ("ecart", "ecarte"), ("gard", "garde")):
        if status.startswith(prefix):
            return kind
    return status


def exploitable(row):
    """Une entreprise qu'on peut vérifier : un site connu, ou un effectif publié par le registre."""
    return bool((row.get("site_web") or "").strip() or str(row.get("effectif_min") or "").strip())


def report(pool, sent, state):
    """Point des vagues : envoyées, résultats de l'enrichissement et de la qualification, rendement, réserve."""
    enrichment = status_by_id("2-enrichissement.csv", "statut")
    qualification = status_by_id("3-qualification.csv", "statut")
    waves = []
    for number in sorted({int(r["vague"]) for r in sent if r.get("vague", "").isdigit()}):
        ids = [r["id"] for r in sent if r.get("vague") == str(number)]
        results = Counter(status_kind(qualification.get(i, "")) for i in ids)
        wave = {"numero": number, "envoyees": len(ids),
                "enrichies": sum(1 for i in ids if i in enrichment),
                "gardees": sum(1 for i in ids if status_kind(enrichment.get(i, "")) == "garde"),
                "qualifiees": results["qualifie"], "a_revoir": results["a revoir"], "ecartees": results["ecarte"],
                # Une vague dont l'enrichissement a tout écarté est jugée : elle n'a rien donné.
                "qualification_faite": any(i in qualification for i in ids)
                or (bool(ids) and all(i in enrichment for i in ids)
                    and not any(status_kind(enrichment.get(i, "")) == "garde" for i in ids))}
        wave["rendement"] = round(wave["qualifiees"] / len(ids), 3) if wave["qualification_faite"] and ids else None
        previous = next((w for w in state.get("vagues", []) if w["numero"] == number), {})
        wave.update({k: previous[k] for k in ("date", "priorites") if k in previous})
        waves.append(wave)
    done = [w for w in waves if w["qualification_faite"]]
    candidates = [r for r in pool if not r.get("exclusion")]
    state.update({
        "mis_a_jour": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "vivier": {"entreprises": len(pool), "exclues": len(pool) - len(candidates),
                   "envoyees": sum(1 for r in candidates if r.get("vague")),
                   "reserve": sum(1 for r in candidates if not r.get("vague")),
                   "reserve_exploitable": sum(1 for r in candidates if not r.get("vague") and exploitable(r))},
        "vagues": waves,
        "total": {"envoyees": sum(w["envoyees"] for w in waves), "qualifiees": sum(w["qualifiees"] for w in waves),
                  "a_revoir": sum(w["a_revoir"] for w in waves),
                  "rendement": round(sum(w["qualifiees"] for w in done) / sum(w["envoyees"] for w in done), 3)
                  if done and sum(w["envoyees"] for w in done) else None},
    })
    return state


def refresh(vivier="vivier.csv"):
    """Met à jour vagues.json d'après les fichiers de la campagne (appelé aussi par assemble_csv.py et export_leads.py,
    pour que le bilan lise toujours des chiffres à jour). Renvoie l'état, ou None sans vivier classé."""
    if not os.path.exists(vivier):
        return None
    pool = read_rows(vivier)
    if not pool or "priorite" not in pool[0]:
        return None
    state = report(pool, read_rows(SENT) if os.path.exists(SENT) else [], read_json("vagues.json", {}))
    write_json("vagues.json", state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--taille", type=int, help="nombre d'entreprises de la réserve à envoyer dans la nouvelle vague")
    action.add_argument("--etat", action="store_true", help="faire le point sans rien envoyer")
    parser.add_argument("--ids", default="", help="avec --taille : identifiants à ajouter en plus, séparés par des virgules")
    parser.add_argument("--objectif", type=int, help="nombre de leads qualifiés visés (gardé dans vagues.json)")
    parser.add_argument("--vague", type=int, help="avec --taille : numéro attendu de la vague ; si cette vague existe "
                                                  "déjà (tâche relancée), rien n'est envoyé une deuxième fois")
    parser.add_argument("--inexploitables", action="store_true",
                        help="avec --taille : compléter la vague avec des entreprises sans site ni effectif connus")
    parser.add_argument("--vivier", default="vivier.csv")
    args = parser.parse_args()

    if not os.path.exists(args.vivier):
        sys.exit(f"VIVIER ABSENT : {args.vivier} (merge_sources.py puis rank_pool.py)")
    pool = read_rows(args.vivier)
    if pool and "priorite" not in pool[0]:
        sys.exit("VIVIER NON CLASSÉ : lancez rank_pool.py après chaque fusion.")
    state = read_json("vagues.json", {})
    if args.objectif:
        state["objectif"] = args.objectif
    sent = read_rows(SENT) if os.path.exists(SENT) else []

    if args.taille is not None:
        if sent and "vague" not in sent[0]:
            sys.exit(f"{SENT} existe déjà sans colonne « vague » : campagne d'un ancien format, à ne pas mélanger.")
        number = max([int(r["vague"]) for r in sent if r.get("vague", "").isdigit()] or [0]) + 1
        if args.vague is not None and args.vague < number:
            print(f"VAGUE {args.vague} DÉJÀ ENVOYÉE : rien n'est ajouté (tâche relancée ?). Point des vagues :")
            args.taille = None
        elif args.vague is not None and args.vague > number:
            sys.exit(f"VAGUE {args.vague} DEMANDÉE, mais la prochaine vague est la {number} : vérifie le numéro.")
    if args.taille is not None:
        forced = {i.strip() for i in args.ids.split(",") if i.strip()}
        unknown = forced - {r["id"] for r in pool}
        if unknown:
            sys.exit(f"IDENTIFIANTS INCONNUS du vivier : {', '.join(sorted(unknown))}")
        reserve = sorted((r for r in pool if not r.get("exclusion") and not r.get("vague")),
                         key=lambda r: int(r["priorite"]))
        usable = [r for r in reserve if exploitable(r)]
        chosen = (usable + [r for r in reserve if not exploitable(r)] if args.inexploitables else usable)[:max(args.taille, 0)]
        if len(chosen) < args.taille and not args.inexploitables:
            print(f"RÉSERVE EXPLOITABLE ÉPUISÉE : {len(usable)} entreprise(s) avec un site ou un effectif connu pour "
                  f"{args.taille} demandées ; {len(reserve) - len(usable)} autre(s) sans site ni effectif. Envoie ce qui "
                  "reste, puis élargis le vivier (sources qui donnent un site ou un effectif, codes ou zones voisins si le "
                  "brief le permet) ou propose au client d'élargir, plutôt que d'envoyer des entreprises invérifiables.")
        chosen += [r for r in pool if r["id"] in forced and not r.get("vague") and r not in chosen]
        if not chosen:
            print("RÉSERVE VIDE : aucune entreprise candidate à envoyer. Élargis le vivier (nouvelles sources, zones "
                  "ou codes voisins) ou arrête la campagne.")
        else:
            for row in chosen:
                row["vague"] = str(number)
            fields = list(sent[0].keys()) if sent else list(pool[0].keys())
            fields += [f for f in pool[0].keys() if f not in fields]
            write_csv(SENT, sent + chosen, fields)
            write_csv(args.vivier, pool, list(pool[0].keys()))
            sent = read_rows(SENT)
            priorities = [int(r["priorite"]) for r in chosen if r.get("priorite")]
            state.setdefault("vagues", []).append({"numero": number, "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                   "priorites": [min(priorities), max(priorities)] if priorities else []})
            print(f"VAGUE {number} : {len(chosen)} entreprises ajoutées à {SENT} (priorités "
                  f"{min(priorities) if priorities else '-'} à {max(priorities) if priorities else '-'}).")

    state = report(pool, sent, state)
    write_json("vagues.json", state)
    print(f"Vivier : {state['vivier']['entreprises']} entreprises, {state['vivier']['exclues']} exclues, "
          f"{state['vivier']['envoyees']} envoyées, {state['vivier']['reserve']} en réserve.")
    for wave in state["vagues"]:
        result = (f"{wave['qualifiees']} qualifiées, {wave['a_revoir']} à revoir, rendement {wave['rendement']}"
                  if wave["qualification_faite"] else "qualification pas encore faite")
        print(f"  Vague {wave['numero']} : {wave['envoyees']} envoyées, {wave['gardees']} gardées ; {result}")
    if state.get("objectif"):
        print(f"Objectif : {state['objectif']} leads qualifiés ; obtenus : {state['total']['qualifiees']}.")


if __name__ == "__main__":
    main()
