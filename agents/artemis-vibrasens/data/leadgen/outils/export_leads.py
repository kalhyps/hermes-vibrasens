"""Livrable d'une campagne : leads.csv et leads.xlsx, une ligne par lead, prête à transmettre au client.

Réunit, toutes vagues confondues :
  - 3-qualification.csv : statut, score, nom commercial, groupe, moyens, certifications, signaux, alerte, pourquoi ce
    lead, axe d'approche, accroche, preuves critère par critère, points à confirmer, sources (fiches du qualificateur) ;
  - 2-enrichissement.csv : site, email, téléphone, activité, effectif annoncé, et, à défaut de ceux du qualificateur,
    nom commercial, groupe, moyens, certifications, signaux (fiches de l'enrichisseur) ;
  - lecture.csv : contacts lus sur le site, avec la page où chacun a été lu, à défaut ;
  - 1-collecte.csv : raison sociale, adresse, numéro officiel, effectif du registre, contacts de la source en dernier recours ;
  - linkedin/contacts.csv et linkedin/contacts_enrichis.csv : les décideurs que le client a validés (jamais ceux « à valider »).
Par défaut : les leads qualifiés, puis ceux à revoir, chacun du meilleur score au moins bon.
- leads.csv : en-têtes en français, séparateur « ; », UTF-8 avec BOM (s'ouvre dans Excel, s'importe dans un CRM) ;
- leads.xlsx : le même, mis en forme (statuts colorés, liens cliquables, filtres), les colonnes techniques masquées
  (score, détail du score, preuves détaillées, vague, identifiant), une feuille « Preuves » (une ligne par lead et par
  critère : verdict, citation, adresse) et une feuille « Méthode » qui explique statuts, colonnes et sources ;
- decideurs.csv et decideurs.xlsx, quand des décideurs ont été validés : une ligne par décideur (entreprise, fonction,
  email et téléphone Dropcontact, profil LinkedIn, source et date pour le RGPD), à envoyer avec leads.xlsx.
L'export met aussi à jour vagues.json (point des vagues lu par le bilan).

Exemples (dans le dossier de la campagne) :
  python export_leads.py
  python export_leads.py --tous          (avec les entreprises écartées à la qualification)
"""
import argparse
import csv
import glob
import json
import os
import re
from datetime import date

from common import country_code, is_generic_email, name_tokens, normalize, read_rows, status_kind
from waves import refresh as refresh_waves

try:
    import phonenumbers
except ImportError:
    phonenumbers = None

# (clé, en-tête, largeur dans le classeur, colonne technique masquée dans le classeur)
LAYOUT = [
    ("rang", "Rang", 6, False), ("statut", "Statut", 10, False),
    ("entreprise", "Entreprise", 26, False), ("raison_sociale", "Raison sociale", 24, False),
    ("groupe", "Groupe", 18, False), ("ville", "Ville", 16, False), ("code_postal", "Code postal", 10, False),
    ("pays", "Pays", 6, False), ("site_web", "Site web", 28, False),
    ("email", "Email de l'entreprise", 26, False), ("telephone", "Téléphone (standard)", 18, False),
    ("origine_contacts", "Où ces coordonnées ont été lues", 42, False),
    ("decideur", "Décideur validé", 22, False), ("fonction_decideur", "Fonction", 22, False),
    ("email_decideur", "Email du décideur", 26, False), ("linkedin_decideur", "Profil LinkedIn", 32, False),
    ("effectif", "Effectif", 22, False), ("activite", "Activité", 40, False),
    ("moyens", "Moyens de production", 40, False), ("certifications", "Certifications", 22, False),
    ("pourquoi", "Pourquoi ce lead", 50, False), ("axe", "Axe d'approche", 26, False),
    ("accroche", "Accroche proposée", 50, False),
    ("signaux", "Signaux récents", 40, False), ("alerte", "Alerte", 24, False),
    ("criteres", "Critères du brief", 44, False), ("a_confirmer", "À confirmer", 40, False),
    ("adresse", "Adresse", 30, False), ("numero_officiel", "Numéro officiel", 18, False),
    ("fiche_registre", "Fiche du registre", 30, False), ("sources", "Sources", 50, False),
    ("score", "Score (classement)", 10, True), ("detail_score", "Détail du score", 40, True),
    ("preuves", "Preuves détaillées", 70, True), ("vague", "Vague", 6, True), ("lu_le", "Site lu le", 11, True),
    ("id", "Identifiant", 14, True),
]
KEYS = [key for key, _, _, _ in LAYOUT]
HEADERS = {key: title for key, title, _, _ in LAYOUT}
ORDER = {"qualifié": 0, "à revoir": 1, "écarté": 2}
REGISTRY_NAMES = {"siren": "SIREN", "kbo": "BCE", "bce": "BCE", "companies_house": "Companies House",
                  "epa_frs": "EPA FRS"}
READ_ON = re.compile(r"^Lu le (\d{4}-\d{2}-\d{2})", re.M)
URL = re.compile(r"https?://[^\s,;)»\"']+")
# Un « à revoir » faible ne part pas dans la liste principale : score sous ce seuil, ou aucun site établi.
WEAK_SCORE = 30
# Tournures de réserve qui n'ont pas leur place dans une accroche (elles vont dans « À confirmer »).
HEDGES = re.compile(r"\b(si (cela|ce|ces|c'est|vous|votre|vos)\b|selon votre|sans préjuger|le cas échéant|éventuellement|"
                    r"peut-être)", re.I)
# Un critère dans le texte assemblé : « Nom : verdict : « citation » (adresse) ; Nom suivant : … » ; les adresses
# contiennent « : », donc « ; » suivi d'une adresse ne coupe pas.
CRITERION_SPLIT = re.compile(r" ; (?=[^;:()«»]{3,80} : )")
VALIDATED = ("valide", "enrichi")  # statuts des contacts LinkedIn qui peuvent figurer dans le livrable
PROOF_HEADERS = ["Rang", "Entreprise", "Critère", "Verdict", "Preuve", "Adresse"]
# Second livrable, decideurs.csv et decideurs.xlsx : une ligne par décideur validé, à envoyer avec leads.xlsx.
CONTACT_LAYOUT = [
    ("rang", "Rang du lead", 8), ("entreprise", "Entreprise", 26), ("ville", "Ville", 16), ("site_web", "Site web", 28),
    ("nom", "Décideur", 24), ("fonction", "Fonction", 26), ("email", "Email professionnel", 30),
    ("qualite_email", "Qualité de l'email", 30), ("telephone", "Téléphone", 18), ("linkedin", "Profil LinkedIn", 44),
    ("statut", "Statut", 30), ("trouve_le", "Trouvé le", 11), ("source", "Source", 36),
]
CONTACT_KEYS = [key for key, _, _ in CONTACT_LAYOUT]
CONTACT_HEADERS = {key: title for key, title, _ in CONTACT_LAYOUT}
# Qualification de l'email renvoyée par Dropcontact, en clair.
EMAIL_QUALITY = {"nominatif@pro": "professionnel, nominatif", "catch_all@pro": "professionnel, serveur « accepte tout » (à confirmer)",
                 "generic@pro": "professionnel, boîte générique", "nominatif@perso": "personnel, nominatif"}


def first(values):
    """Premier élément d'une liste « a ; b ; c » de lecture.csv."""
    return (values or "").split(" ; ")[0]


def score_of(row):
    try:
        return float(row.get("score") or 0)
    except ValueError:
        return 0.0


def pick(*values):
    return next((v for v in values if v), "")


def headcount(base, lead, more):
    """Effectif du registre et effectif annoncé sur le site, avec leur origine."""
    parts = []
    if base.get("effectif"):
        parts.append(f"{base['effectif']} (registre)")
    site = pick(lead.get("effectif_site"), more.get("effectif_site"))
    if site:
        parts.append(f"{site} (site)")
    return " ; ".join(parts)


def read_date(lead_id):
    path = os.path.join("pages", f"{lead_id}.md")
    try:
        with open(path, encoding="utf-8") as handle:
            match = READ_ON.search(handle.read(4000))
    except OSError:
        return ""
    return match.group(1) if match else ""


def official_number(base):
    """« SIREN 123456789 », « BCE 0123.456.789 »… : colonnes id_registre et registre, ou SIREN des anciennes campagnes."""
    if base.get("id_registre"):
        registry = REGISTRY_NAMES.get((base.get("registre") or "").lower(), base.get("registre", ""))
        return f"{registry} {base['id_registre']}".strip()
    if base.get("siret") or (base.get("pays") == "FR" and re.fullmatch(r"\d{9}", base.get("id", ""))):
        return f"SIREN {base['id']}"
    return ""


def phone_format(number, country):
    """Numéro au format international du pays (« +33 4 74 81 93 90 ») ; inchangé s'il ne se lit pas."""
    if not number or phonenumbers is None:
        return number or ""
    try:
        parsed = phonenumbers.parse(number, country or None)
    except phonenumbers.NumberParseException:
        return number
    if not phonenumbers.is_valid_number(parsed):
        return number
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)


# --- coordonnées et leur origine -------------------------------------------------------------------------------

def contact_email(candidates):
    """Premier email générique utilisable (pas de boîte RGPD, technique ou nominative) parmi (valeur, origine)."""
    for value, origin in candidates:
        for email in re.split(r"\s*;\s*|\s*,\s*", value or ""):
            if is_generic_email(email.strip()):
                return email.strip(), origin
    return "", ""


def contact_phone(candidates):
    for value, origin in candidates:
        if value:
            return value, origin
    return "", ""


def pages_of(reading):
    """{valeur: page} depuis la colonne contacts_pages de lecture.csv (« valeur : adresse ; … »)."""
    pages = {}
    for item in (reading.get("contacts_pages") or "").split(" ; "):
        value, sep, page = item.partition(" : ")
        if sep and value.strip():
            pages[value.strip()] = page.strip()
    return pages


def source_label(base):
    """D'où vient une coordonnée de la collecte, en clair pour le client."""
    name = (base.get("source_nom") or "").lower()
    if "overture" in name:
        return "base de lieux Overture, non vérifié sur le site"
    if "registre" in name:
        return "registre des entreprises, non vérifié sur le site"
    return f"source de collecte « {base.get('source_nom', '')} », non vérifié sur le site"


def origin_text(kind, value, origin, pages):
    """« email : lu sur https://…/contact », ou l'origine générique quand la page n'est pas connue."""
    if not value:
        return ""
    page = pages.get(value)
    return f"{kind} : lu sur {page}" if page else f"{kind} : {origin}"


# --- critères et preuves ---------------------------------------------------------------------------------------

def criteria_by_id(folder="qualification"):
    """{id: {critère: verdict et preuve}} depuis les fiches JSON du qualificateur, fusionnées comme dans
    assemble_csv.py (les lots d'abord, puis les fiches <id>.json, chacun par date)."""
    merged = {}
    paths = sorted(glob.glob(os.path.join(folder, "*.json")),
                   key=lambda p: (not os.path.basename(p).startswith(("lot-", "vague-")), os.path.getmtime(p)))
    for path in paths:
        try:
            with open(path, encoding="utf-8") as handle:
                content = json.load(handle)
        except (OSError, ValueError):
            continue
        for record in content if isinstance(content, list) else [content]:
            if isinstance(record, dict) and record.get("id") not in (None, "") and isinstance(record.get("criteres"), dict):
                merged.setdefault(str(record["id"]).strip(), {}).update(record["criteres"])
    return merged


def split_criteria(text):
    """Repli sur le texte assemblé quand les fiches JSON manquent : [(critère, verdict et preuve)]."""
    items = []
    for piece in CRITERION_SPLIT.split(text or ""):
        name, sep, value = piece.partition(" : ")
        if sep:
            items.append((name.strip(), value.strip()))
    return items


def verdict_of(value):
    """« oui : « citation » (adresse) » -> (« oui », « « citation » (adresse) »)."""
    verdict, sep, proof = str(value or "").partition(" : ")
    verdict = verdict.strip()
    if not sep or len(verdict) > 40:
        return "", str(value or "")
    return verdict, proof.strip()


def proof_rows(rank, company, criteria):
    """Lignes de la feuille « Preuves » : une par critère, citation et adresses séparées."""
    rows = []
    for name, value in criteria:
        verdict, proof = verdict_of(value)
        urls = [u.rstrip(".") for u in URL.findall(proof)]
        text = URL.sub("", proof)
        text = re.sub(r"\(\s*(?:;\s*)*\)", "", text)  # parenthèses vidées de leurs adresses
        rows.append([rank, company, name, verdict, " ".join(text.split()), " ; ".join(dict.fromkeys(urls))])
    return rows


def unique_sources(*fields):
    """Toutes les adresses citées (sources et preuves), une seule fois, dans l'ordre."""
    seen = []
    for field in fields:
        for url in URL.findall(field or ""):
            url = url.rstrip(".")
            if url not in seen:
                seen.append(url)
    return " ; ".join(seen)


def reasons(lead, more, effectif, signals):
    """« Pourquoi ce lead » : les raisons écrites par le qualificateur ; à défaut, les faits chiffrés qu'il a prouvés."""
    if lead.get("pourquoi"):
        return lead["pourquoi"]
    facts = [pick(lead.get("moyens"), more.get("moyens")), f"effectif {effectif}" if effectif else "",
             pick(lead.get("certifications"), more.get("certifications")), first(signals)]
    return " ; ".join(f.strip().rstrip(".") for f in facts if f and f.strip())


# --- décideurs validés (LinkedIn) ------------------------------------------------------------------------------

def validated_contacts(folder="linkedin"):
    """Décideurs validés par le client (statuts valide ou enrichi), avec l'email trouvé par Dropcontact s'il existe :
    {entreprise normalisée: [contact, …]}. Un contact « à valider » ou rejeté n'entre jamais dans le livrable."""
    base = os.path.join(folder, "contacts.csv")
    if not os.path.exists(base):
        return {}
    contacts = {}
    for row in read_rows(base):
        if normalize(row.get("statut", "")) in VALIDATED:
            contacts[row.get("linkedin_url") or row.get("nom")] = dict(row)
    enriched = os.path.join(folder, "contacts_enrichis.csv")
    if os.path.exists(enriched):
        for row in read_rows(enriched):
            key = row.get("linkedin_url") or row.get("nom")
            merged = {**contacts.get(key, {}), **{k: v for k, v in row.items() if v}}
            if normalize(merged.get("statut", "")) in VALIDATED:
                contacts[key] = merged
    by_company = {}
    for contact in contacts.values():
        by_company.setdefault(normalize(contact.get("entreprise", "")), []).append(contact)
    return by_company


def contacts_for(by_company, *names):
    """Contacts d'une entreprise, par son nom commercial ou sa raison sociale (mêmes mots distinctifs)."""
    for name in names:
        if name and normalize(name) in by_company:
            return by_company[normalize(name)]
    wanted = {tuple(name_tokens(n)) for n in names if n}
    return next((c for k, c in by_company.items() if tuple(name_tokens(k)) in wanted), [])


def contact_row(person, lead):
    """Une ligne de decideurs.csv : le décideur validé, rattaché à son entreprise et à son rang dans leads.csv."""
    email = person.get("email", "")
    quality = person.get("qualification_email", "")
    return {"rang": lead.get("rang", ""), "entreprise": lead.get("entreprise") or person.get("entreprise", ""),
            "ville": lead.get("ville", ""), "site_web": lead.get("site_web") or person.get("site_web", ""),
            "nom": person.get("nom", ""), "fonction": person.get("intitule", ""), "email": email,
            "qualite_email": EMAIL_QUALITY.get(quality, quality) if email else "",
            "telephone": person.get("telephone", ""), "linkedin": person.get("linkedin_url", ""),
            "statut": "validé par le client, email trouvé" if email else "validé par le client, email non trouvé",
            "trouve_le": person.get("date_collecte", ""), "source": person.get("source", "")}


def contact_rows(rows, by_company):
    """Les décideurs validés dans l'ordre des leads, puis ceux dont l'entreprise n'est pas dans le livrable."""
    out, seen = [], set()
    for row in rows:
        for person in contacts_for(by_company, row["entreprise"], row["raison_sociale"]):
            key = person.get("linkedin_url") or person.get("nom")
            if key not in seen:
                seen.add(key)
                out.append(contact_row(person, row))
    for people in by_company.values():
        for person in people:
            key = person.get("linkedin_url") or person.get("nom")
            if key not in seen:
                seen.add(key)
                out.append(contact_row(person, {}))
    return out


def write_contacts(contacts, campaign, separator):
    """decideurs.csv et decideurs.xlsx (feuilles Décideurs et Méthode) ; ne produit rien sans décideur validé."""
    if not contacts:
        return False
    with open("decideurs.csv", "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=separator)
        writer.writerow([CONTACT_HEADERS[k] for k in CONTACT_KEYS])
        writer.writerows([[c.get(k, "") for k in CONTACT_KEYS] for c in contacts])
    try:
        import xlsxwriter
    except ImportError:
        return True
    book = xlsxwriter.Workbook("decideurs.xlsx", {"strings_to_urls": False, "strings_to_formulas": False,
                                                  "strings_to_numbers": False})
    head = book.add_format({"bold": True, "bg_color": "#1F3864", "font_color": "#FFFFFF", "text_wrap": True,
                            "valign": "top", "border": 1})
    cell = book.add_format({"text_wrap": True, "valign": "top"})
    link = book.add_format({"font_color": "#0563C1", "underline": 1, "valign": "top"})
    sheet = book.add_worksheet("Décideurs")
    for col, (key, title, width) in enumerate(CONTACT_LAYOUT):
        sheet.write(0, col, title, head)
        sheet.set_column(col, col, width)
    for line, contact in enumerate(contacts, 1):
        for col, key in enumerate(CONTACT_KEYS):
            value = str(contact.get(key, ""))
            if key == "rang" and value.isdigit():
                sheet.write_number(line, col, float(value), cell)
            elif key in ("site_web", "linkedin") and value.startswith("http"):
                sheet.write_url(line, col, value, link, string=value)
            elif key == "email" and "@" in value:
                sheet.write_url(line, col, f"mailto:{value}", link, string=value)
            else:
                sheet.write_string(line, col, value, cell)
    sheet.freeze_panes(1, 2)
    sheet.autofilter(0, 0, max(len(contacts), 1), len(CONTACT_LAYOUT) - 1)
    about = book.add_worksheet("Méthode")
    about.set_column(0, 0, 120)
    lines = [
        f"Campagne : {campaign}",
        f"Export du {date.today().isoformat()} : {len(contacts)} décideur(s) validé(s), "
        f"{sum(1 for c in contacts if c['email'])} avec un email professionnel.",
        "",
        "Une ligne par décideur que vous avez validé vous-même, rattaché à son entreprise de leads.xlsx (« Rang du lead »).",
        "« Décideur », « Fonction », « Profil LinkedIn » : le profil public trouvé par moteur de recherche (titre et extrait "
        "seulement, sans connexion à LinkedIn) ; vérifiez la fonction sur le profil avant d'écrire.",
        "« Email professionnel », « Téléphone » : trouvés par Dropcontact à partir du nom, de l'entreprise et de son site ; "
        "« Qualité de l'email » dit si l'adresse est nominative et vérifiée, ou si le serveur accepte tout (à confirmer).",
        "RGPD : « Source » et « Trouvé le » gardent l'origine des données. Informez chaque personne, dès votre premier "
        "message, que ses coordonnées professionnelles viennent de sources publiques et de Dropcontact, et qu'elle peut "
        "s'y opposer en répondant. Supprimez les contacts sans réponse après trois ans au plus.",
        "Les contacts non validés ou rejetés ne figurent dans aucun fichier remis.",
    ]
    for number, text in enumerate(lines):
        about.write_string(number, 0, text, cell)
    book.close()
    return True


# --- lignes du livrable ----------------------------------------------------------------------------------------

def build_rows(leads, collecte, enrich, reading, criteria, contacts):
    rows, proofs = [], []
    for rank, lead in enumerate(leads, 1):
        base, more, site = collecte.get(lead["id"], {}), enrich.get(lead["id"], {}), reading.get(lead["id"], {})
        country = country_code(base.get("pays"))
        legal_name = base.get("nom") or lead.get("nom", "")
        trade_name = pick(lead.get("nom_commercial"), more.get("nom_commercial"))
        company = trade_name or lead.get("nom") or legal_name
        pages = pages_of(site)
        from_site = "lu sur le site de l'entreprise"
        email, email_origin = contact_email([(lead.get("email"), "relevé par le qualificateur (voir Sources)"),
                                             (more.get("email_generique"), from_site),
                                             (site.get("emails_generiques"), from_site),
                                             (base.get("email"), source_label(base))])
        phone, phone_origin = contact_phone([(lead.get("telephone"), "relevé par le qualificateur (voir Sources)"),
                                             (more.get("telephone"), from_site),
                                             (first(site.get("telephones")), from_site),
                                             (base.get("telephone"), source_label(base))])
        effectif = headcount(base, lead, more)
        signals = pick(lead.get("signaux"), more.get("signaux"))
        items = list(criteria.get(lead["id"], {}).items()) or split_criteria(lead.get("criteres", ""))
        people = contacts_for(contacts, trade_name, legal_name, lead.get("nom"))
        rows.append({
            "rang": rank, "statut": lead.get("statut", ""), "score": lead.get("score", ""),
            "entreprise": company,
            "raison_sociale": legal_name if trade_name and legal_name and trade_name.lower() != legal_name.lower() else "",
            "groupe": pick(lead.get("groupe"), more.get("groupe")),
            "ville": base.get("ville", ""), "code_postal": base.get("code_postal", ""), "pays": base.get("pays", ""),
            "site_web": pick(lead.get("site_web"), more.get("site_web")),
            "email": email, "telephone": phone_format(phone, country),
            "origine_contacts": " ; ".join(t for t in (origin_text("email", email, email_origin, pages),
                                                       origin_text("téléphone", phone, phone_origin, pages)) if t),
            "decideur": " ; ".join(p.get("nom", "") for p in people),
            "fonction_decideur": " ; ".join(p.get("intitule", "") for p in people),
            "email_decideur": " ; ".join(p.get("email") or "non trouvé" for p in people),
            "linkedin_decideur": " ; ".join(p.get("linkedin_url", "") for p in people),
            "effectif": effectif,
            "activite": pick(lead.get("activite"), more.get("activite")),
            "moyens": pick(lead.get("moyens"), more.get("moyens")),
            "certifications": pick(lead.get("certifications"), more.get("certifications")),
            "pourquoi": reasons(lead, more, effectif, signals),
            "axe": lead.get("axe", ""),
            "accroche": lead.get("accroche", ""),
            "signaux": signals,
            "alerte": pick(lead.get("alerte"), more.get("alerte")),
            "criteres": " ; ".join(f"{name} : {verdict_of(value)[0] or 'voir preuves'}" for name, value in items),
            "preuves": lead.get("criteres", ""),
            "a_confirmer": pick(lead.get("a_verifier"), lead.get("a_confirmer")),
            "adresse": base.get("adresse", ""), "numero_officiel": official_number(base),
            "fiche_registre": base.get("source", "") if base.get("source", "").startswith("http") else "",
            "sources": unique_sources(lead.get("criteres"), lead.get("sources")),
            "detail_score": lead.get("raisons", ""), "vague": base.get("vague", ""), "lu_le": read_date(lead["id"]),
            "id": lead["id"],
        })
        proofs += proof_rows(rank, company, items)
    return rows, proofs


def repeated_phrase(texts, size=6):
    """La suite de `size` mots la plus partagée entre les textes, et le nombre de textes qui la contiennent : une
    accroche dont le cœur se retrouve chez un tiers des leads est une formule, pas une accroche."""
    seen = {}
    for text in texts:
        words = re.findall(r"[\w'’-]+", text.lower())
        for gram in {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)}:
            seen[gram] = seen.get(gram, 0) + 1
    return max(seen.items(), key=lambda item: item[1], default=("", 0))


def is_weak(row):
    """« À revoir » trop incertain pour la liste principale : score sous WEAK_SCORE, ou aucun site établi."""
    return row["statut"] == "à revoir" and (score_of(row) < WEAK_SCORE or not row.get("site_web"))


def write_xlsx(path, rows, proofs, campaign, counts, weak=()):
    """Classeur mis en forme ; renvoie False si la bibliothèque xlsxwriter n'est pas installée."""
    try:
        import xlsxwriter
    except ImportError:
        return False
    # Texte tel quel : un téléphone « +33 4… » ou une citation « = … » ne doit pas devenir une formule.
    book = xlsxwriter.Workbook(path, {"strings_to_urls": False, "strings_to_formulas": False, "strings_to_numbers": False})
    head = book.add_format({"bold": True, "bg_color": "#1F3864", "font_color": "#FFFFFF", "text_wrap": True,
                            "valign": "top", "border": 1})
    technical = book.add_format({"bold": True, "bg_color": "#7F7F7F", "font_color": "#FFFFFF", "text_wrap": True,
                                 "valign": "top", "border": 1})
    cell = book.add_format({"text_wrap": True, "valign": "top"})
    link = book.add_format({"font_color": "#0563C1", "underline": 1, "valign": "top"})
    status_formats = {"qualifié": book.add_format({"bg_color": "#E2EFDA", "valign": "top", "bold": True}),
                      "à revoir": book.add_format({"bg_color": "#FFF2CC", "valign": "top", "bold": True}),
                      "écarté": book.add_format({"bg_color": "#F2F2F2", "valign": "top"})}
    alert = book.add_format({"text_wrap": True, "valign": "top", "font_color": "#C00000", "bold": True})
    verdict_formats = {"oui": book.add_format({"bg_color": "#E2EFDA", "valign": "top"}),
                       "non": book.add_format({"bg_color": "#F8CBAD", "valign": "top"}),
                       "inconnu": book.add_format({"bg_color": "#FFF2CC", "valign": "top"})}

    def leads_sheet(name, rows):
        sheet = book.add_worksheet(name)
        for col, (key, title, width, hidden) in enumerate(LAYOUT):
            sheet.write(0, col, title, technical if hidden else head)
            sheet.set_column(col, col, width, None, {"hidden": hidden})
        write_leads(sheet, rows)
        sheet.freeze_panes(1, 3)
        sheet.autofilter(0, 0, max(len(rows), 1), len(LAYOUT) - 1)

    def write_leads(sheet, rows):
        for line, row in enumerate(rows, 1):
            for col, key in enumerate(KEYS):
                value = row.get(key, "")
                if key == "statut":
                    sheet.write(line, col, value, status_formats.get(value, cell))
                elif key in ("rang", "score") and str(value).replace(".", "", 1).isdigit():
                    sheet.write_number(line, col, float(value), cell)
                elif key in ("site_web", "fiche_registre") and str(value).startswith("http"):
                    sheet.write_url(line, col, value, link, string=value)
                elif key == "linkedin_decideur" and str(value).startswith("http") and ";" not in str(value):
                    sheet.write_url(line, col, value, link, string=value)
                elif key in ("email", "email_decideur") and "@" in str(value) and ";" not in str(value):
                    sheet.write_url(line, col, f"mailto:{value}", link, string=value)
                elif key == "alerte" and value:
                    sheet.write_string(line, col, str(value), alert)
                else:
                    sheet.write_string(line, col, str(value), cell)

    leads_sheet("Leads", rows)
    if weak:
        leads_sheet("À revoir, faibles", weak)

    evidence = book.add_worksheet("Preuves")
    for col, (title, width) in enumerate(zip(PROOF_HEADERS, (6, 26, 30, 14, 70, 50))):
        evidence.write(0, col, title, head)
        evidence.set_column(col, col, width)
    for line, proof in enumerate(proofs, 1):
        for col, value in enumerate(proof):
            if col == 0 and str(value).isdigit():
                evidence.write_number(line, col, float(value), cell)
            elif col == 3:
                key = next((k for k in ("oui", "non", "inconnu") if str(value).lower().startswith(k)), None)
                evidence.write_string(line, col, str(value), verdict_formats.get(key, cell))
            else:
                evidence.write_string(line, col, str(value), cell)
    evidence.freeze_panes(1, 2)
    evidence.autofilter(0, 0, max(len(proofs), 1), len(PROOF_HEADERS) - 1)

    about = book.add_worksheet("Méthode")
    about.set_column(0, 0, 120)
    lines = [
        f"Campagne : {campaign}",
        f"Export du {date.today().isoformat()} : " + ", ".join(f"{n} {s}" for s, n in counts.items() if n),
        "",
        "Feuille « Leads » : une ligne par entreprise, les qualifiées d'abord, puis celles à revoir ; dans chaque "
        "groupe, de la plus intéressante à la moins intéressante (« Rang »).",
        "Statut « qualifié » : chaque critère indispensable du brief est prouvé par une source citée.",
        f"Feuille « À revoir, faibles » (quand elle existe) : les entreprises à revoir dont le score est sous {WEAK_SCORE}/100 "
        "ou dont aucun site n'a été établi ; gardées pour mémoire, hors de la liste à appeler.",
        "Statut « à revoir » : rien d'éliminatoire, mais au moins un critère indispensable reste à confirmer ; ce qu'il "
        "faut vérifier est écrit dans « À confirmer » (souvent au premier appel).",
        "« Pourquoi ce lead » : les faits chiffrés et prouvés qui font de l'entreprise un lead pour votre offre. "
        "« Axe d'approche » : celui des axes du brief (un problème que votre offre résout) que ces faits appuient le "
        "mieux. « Accroche proposée » : une ouverture que le commercial peut reprendre, appuyée sur ces faits et sur cet axe.",
        "« Critères du brief » : le verdict pour chaque critère (oui, non, inconnu) ; la citation et l'adresse de chaque "
        "preuve sont dans la feuille « Preuves ».",
        "« Signaux récents » : faits datés, chacun avec le lien de sa source ; un fait sans date ni lien n'y figure pas.",
        "« Email de l'entreprise » et « Téléphone (standard) » : les coordonnées génériques de l'entreprise (accueil, "
        "contact, commercial), jamais celles d'une personne ; « Où ces coordonnées ont été lues » donne la page, ou la "
        "base d'où elles viennent quand le site ne les publie pas.",
        "« Décideur validé », « Fonction », « Email du décideur », « Profil LinkedIn » : les seules personnes que vous "
        "avez validées vous-même (profil public LinkedIn, ou dirigeant publié au registre des entreprises pour les "
        "petites structures), avec l'email professionnel trouvé par Dropcontact ; informez chaque personne de "
        "l'origine de ses données dès le premier message et de son droit de s'opposer à tout nouveau contact (RGPD).",
        "« Effectif » : tranche du registre et, s'il est annoncé, l'effectif du site web.",
        "« Numéro officiel » et « Fiche du registre » : l'entreprise au registre officiel ; « Sources » : toutes les "
        "adresses consultées.",
        "Colonnes techniques masquées (score de classement sur 100, détail du score, preuves détaillées, vague, date de "
        "lecture du site, identifiant) : clic droit sur les en-têtes puis « Afficher ».",
    ]
    for number, text in enumerate(lines):
        about.write_string(number, 0, text, cell)
    book.close()
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sortie", default="leads.csv")
    parser.add_argument("--tous", action="store_true", help="inclure les entreprises écartées à la qualification")
    parser.add_argument("--separateur", default=";", help="séparateur des colonnes (« ; » pour Excel en français)")
    args = parser.parse_args()

    if not os.path.exists("3-qualification.csv"):
        raise SystemExit("3-qualification.csv est absent : assemblez d'abord la qualification.")
    collecte = {r["id"]: r for r in read_rows("1-collecte.csv")} if os.path.exists("1-collecte.csv") else {}
    enrich = {r["id"]: r for r in read_rows("2-enrichissement.csv")} if os.path.exists("2-enrichissement.csv") else {}
    reading = {r["id"]: r for r in read_rows("lecture.csv")} if os.path.exists("lecture.csv") else {}
    leads = read_rows("3-qualification.csv")
    for lead in leads:
        lead["statut"] = status_kind(lead.get("statut", "")) or lead.get("statut", "")
    leads = [r for r in leads if args.tous or r["statut"] in ("qualifié", "à revoir")]
    leads.sort(key=lambda r: (ORDER.get(r.get("statut"), 3), -score_of(r)))
    contacts = validated_contacts()
    rows, proofs = build_rows(leads, collecte, enrich, reading, criteria_by_id(), contacts)
    weak_ids = {r["id"] for r in rows if is_weak(r)}
    weak = []
    if weak_ids:  # numérotées à part, pour que la liste principale garde des rangs continus
        rows, proofs = build_rows([l for l in leads if l["id"] not in weak_ids], collecte, enrich, reading,
                                  criteria_by_id(), contacts)
        weak, _ = build_rows([l for l in leads if l["id"] in weak_ids], collecte, enrich, reading, criteria_by_id(),
                             contacts)

    with open(args.sortie, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=args.separateur)
        writer.writerow([HEADERS[k] for k in KEYS])
        writer.writerows([[row.get(k, "") for k in KEYS] for row in rows])
    counts = {s: sum(r["statut"] == s for r in rows) for s in ORDER}
    print(f"{len(rows)} leads écrits dans {args.sortie} : " + ", ".join(f"{n} {s}" for s, n in counts.items() if n))
    weak_path = os.path.splitext(args.sortie)[0] + "-a-revoir-faibles.csv"
    if weak:
        with open(weak_path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=args.separateur)
            writer.writerow([HEADERS[k] for k in KEYS])
            writer.writerows([[row.get(k, "") for k in KEYS] for row in weak])
        print(f"{len(weak)} « à revoir » faibles (score sous {WEAK_SCORE} ou sans site) mis à part : {weak_path} et "
              "feuille « À revoir, faibles » du classeur.")
    elif os.path.exists(weak_path):
        os.remove(weak_path)
    workbook = os.path.splitext(args.sortie)[0] + ".xlsx"
    if write_xlsx(workbook, rows, proofs, os.path.basename(os.getcwd()), counts, weak):
        print(f"Classeur mis en forme : {workbook} (feuilles Leads, Preuves, Méthode)")
    else:
        print("Classeur .xlsx non produit : la bibliothèque xlsxwriter n'est pas installée (image à reconstruire).")
    people = contact_rows(rows, contacts)
    if write_contacts(people, os.path.basename(os.getcwd()), args.separateur):
        print(f"Décideurs validés : {len(people)} dans decideurs.csv et decideurs.xlsx (à envoyer avec {workbook}), "
              f"sur {sum(1 for r in rows if r['decideur'])} lead(s).")
    else:
        print("Aucun décideur validé : pas de fichier decideurs.")
    checks = [
        ("Sans email ni téléphone", [r for r in rows if not (r["email"] or r["telephone"])]),
        ("Leads sans « Pourquoi ce lead »", [r for r in rows if not r["pourquoi"]]),
        ("Leads sans accroche", [r for r in rows if not r["accroche"]]),
        ("Leads à revoir sans « à confirmer »", [r for r in rows if r["statut"] == "à revoir" and not r["a_confirmer"]]),
        ("Leads sans preuve citée", [r for r in rows if not r["sources"]]),
    ]
    if any(r["axe"] for r in rows):
        checks.append(("Leads sans axe d'approche", [r for r in rows if not r["axe"]]))
    checks.append(("Accroches avec une réserve (« si cela… », « selon votre… »)",
                   [r for r in rows if HEDGES.search(r["accroche"])]))
    for label, missing in checks:
        if missing:
            print(f"{label} : {len(missing)} ({', '.join(r['entreprise'] for r in missing[:8])})")
    hooks = [r["accroche"] for r in rows if r["accroche"]]
    phrase, shared = repeated_phrase(hooks)
    if len(hooks) >= 6 and shared >= max(3, len(hooks) // 3):
        print(f"Accroches passe-partout : « {phrase} » revient dans {shared} accroches sur {len(hooks)} ; "
              "chacune doit dire ce que l'offre change pour cette entreprise, sur son axe.")
    qualified = [str(r["score"]) for r in rows if r["statut"] == "qualifié" and str(r["score"]).strip()]
    if len(qualified) >= 6:
        top, same = max(((s, qualified.count(s)) for s in set(qualified)), key=lambda item: item[1])
        if same >= max(4, len(qualified) // 3):
            print(f"Classement peu discriminant : {same} leads qualifiés sur {len(qualified)} ont le même score "
                  f"({top}/100) ; revoir la répartition des points entre les critères appréciés.")
    state = refresh_waves()
    if state:
        print("Point des vagues mis à jour (vagues.json) : " + " ; ".join(
            f"vague {w['numero']} : {w['envoyees']} envoyées, {w['gardees']} gardées, {w['qualifiees']} qualifiées"
            for w in state.get("vagues", [])))


if __name__ == "__main__":
    main()
