"""Tests hors ligne : python3 -m unittest discover -s tests"""
import csv
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import linkedin_search as ls  # noqa: E402
import enrich_dropcontact as ed  # noqa: E402

def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


COMPANY_RESULTS = [
    {"title": "Usinage Dupont | LinkedIn",
     "url": "https://fr.linkedin.com/company/usinage-dupont/",
     "snippet": "Usinage Dupont | 1 245 abonnés sur LinkedIn. Mécanique de précision depuis 1978. | Secteur : Fabrication de machines · Taille de l’entreprise : 51-200 employés · Siège social : Annecy"},
    {"title": "Präzisionstechnik Müller GmbH - LinkedIn",
     "url": "https://de.linkedin.com/company/praezisionstechnik-mueller/about",
     "snippet": "Industry: Machinery Manufacturing | Company size: 201-500 employees | Headquarters: Stuttgart"},
    {"title": "Offre d'emploi tourneur", "url": "https://www.linkedin.com/jobs/view/123", "snippet": ""},
]

PEOPLE_RESULTS = [
    {"title": "Marie Martin - Directrice industrielle - Usinage Dupont | LinkedIn",
     "url": "https://fr.linkedin.com/in/marie-martin-12ab/?originalSubdomain=fr", "snippet": "Annecy · Usinage Dupont"},
    {"title": "Paul Durand – Usinage Dupont | LinkedIn",
     "url": "https://fr.linkedin.com/in/pauldurand", "snippet": "Plant Manager chez Usinage Dupont · Expérience"},
    {"title": "Jean Autre - CEO - Autre Société | LinkedIn",
     "url": "https://fr.linkedin.com/in/jean-autre", "snippet": "Ancien de Machin"},
    {"title": "50+ profils « Usinage Dupont » | LinkedIn",
     "url": "https://fr.linkedin.com/in/pub/dir", "snippet": "Usinage Dupont"},
    {"title": "Luc Petit - Technicien méthodes - Usinage Dupont | LinkedIn",
     "url": "https://fr.linkedin.com/in/luc-petit", "snippet": ""},
]


class ParsingTests(unittest.TestCase):
    def test_company_fr(self):
        r = ls.parse_company_result(COMPANY_RESULTS[0])
        self.assertEqual(r["entreprise"], "Usinage Dupont")
        self.assertEqual(r["linkedin_url"], "https://www.linkedin.com/company/usinage-dupont")
        self.assertEqual(r["secteur"], "Fabrication de machines")
        self.assertEqual(r["taille"], "51-200 employés")
        self.assertEqual(r["siege"], "Annecy")

    def test_company_en_about_page(self):
        r = ls.parse_company_result(COMPANY_RESULTS[1])
        self.assertEqual(r["entreprise"], "Präzisionstechnik Müller GmbH")
        self.assertEqual(r["linkedin_url"], "https://www.linkedin.com/company/praezisionstechnik-mueller")
        self.assertEqual(r["secteur"], "Machinery Manufacturing")
        self.assertEqual(r["taille"], "201-500 employees")

    def test_non_company_ignored(self):
        self.assertIsNone(ls.parse_company_result(COMPANY_RESULTS[2]))

    def test_profiles(self):
        parsed = [ls.parse_profile_result(r, "Usinage Dupont") for r in PEOPLE_RESULTS]
        marie, paul, other, directory, luc = parsed
        self.assertEqual(marie["nom"], "Marie Martin")
        self.assertEqual(marie["intitule"], "Directrice industrielle")
        self.assertEqual(marie["linkedin_url"], "https://www.linkedin.com/in/marie-martin-12ab")
        self.assertEqual(paul["intitule"], "Plant Manager")
        self.assertIsNone(other)       # autre entreprise
        self.assertIsNone(directory)   # page annuaire
        self.assertTrue(ls.is_decision_maker(marie["intitule"]))
        self.assertTrue(ls.is_decision_maker(paul["intitule"]))
        self.assertFalse(ls.is_decision_maker(luc["intitule"]))
        self.assertTrue(ls.is_decision_maker("Geschäftsführer"))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.seen = os.path.join(self.tmp, "seen.json")

    def run_cli(self, *argv):
        ls.main(["--seen", self.seen, *argv])

    def test_companies_dedup_across_runs(self):
        out = os.path.join(self.tmp, "c.csv")
        with mock.patch.object(ls, "search", return_value=COMPANY_RESULTS):
            self.run_cli("companies", "--keywords", "usinage", "--out", out, "--pause", "0")
            self.run_cli("companies", "--keywords", "usinage", "--out", out, "--pause", "0")
        rows = read(out)
        self.assertEqual(len(rows), 2)  # 2e passage : tout est déjà vu
        self.assertEqual(rows[0]["statut"], "a_trier")
        deja = read(os.path.join(self.tmp, "deja_vues.csv"))  # ce qui a été écarté reste traçable
        self.assertEqual([d["linkedin_url"] for d in deja], [r["linkedin_url"] for r in rows])
        self.assertTrue(all(d["type"] == "entreprise" and d["vu_le"] for d in deja))

    def test_revoir_reprend_les_deja_vues(self):
        out = os.path.join(self.tmp, "c.csv")
        with mock.patch.object(ls, "search", return_value=COMPANY_RESULTS):
            self.run_cli("companies", "--keywords", "usinage", "--out", out, "--pause", "0")
            self.run_cli("--revoir", "companies", "--keywords", "usinage", "--out", out, "--pause", "0")
        self.assertEqual(len(read(out)), 4)
        self.assertEqual(len(read(os.path.join(self.tmp, "deja_vues.csv"))), 2)

    def test_people_and_links(self):
        comp = os.path.join(self.tmp, "c.csv")
        ls.append_csv(comp, [{"entreprise": "Usinage Dupont",
                              "linkedin_url": "https://www.linkedin.com/company/usinage-dupont"}],
                      ["entreprise", "linkedin_url"])
        out = os.path.join(self.tmp, "p.csv")
        with mock.patch.object(ls, "search", return_value=PEOPLE_RESULTS):
            self.run_cli("people", "--input", comp, "--out", out, "--pause", "0", "--decideurs-only")
        rows = read(out)
        self.assertEqual([r["nom"] for r in rows], ["Marie Martin", "Paul Durand"])
        self.assertTrue(all(r["statut"] == "a_valider" for r in rows))

        links = os.path.join(self.tmp, "l.csv")
        self.run_cli("liens", "--input", comp, "--out", links, "--titles", "CEO", "Plant Manager")
        row = read(links)[0]
        self.assertEqual(row["booleen_sales_navigator"], '(CEO OR "Plant Manager")')
        self.assertTrue(row["page_linkedin_personnes"].startswith(
            "https://www.linkedin.com/company/usinage-dupont/people/?keywords="))


class LeadsCsvTests(unittest.TestCase):
    """Les décideurs se cherchent dans leads.csv (livrable de la qualification), pour les seuls leads qualifiés."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.seen = os.path.join(self.tmp, "seen.json")
        self.leads = os.path.join(self.tmp, "leads.csv")
        with open(self.leads, "w", newline="", encoding="utf-8-sig") as f:
            f.write("Rang;Statut;Entreprise;Site web;Vague\n")
            f.write("1;qualifié;Usinage Dupont;https://usinage-dupont.fr;1\n")
            f.write("2;à revoir;Autre Société;https://autre.fr;1\n")
            f.write("3;qualifié;Autre Société;https://autre.fr;2\n")

    def test_lecture_filtree(self):
        rows = ls.read_companies(self.leads, [("statut", "Qualifie"), ("Vague", "1")])
        self.assertEqual(rows, [{"entreprise": "Usinage Dupont", "site_web": "https://usinage-dupont.fr", "linkedin_url": ""}])
        self.assertEqual(len(ls.read_companies(self.leads, [("Statut", "qualifié")])), 2)
        with self.assertRaises(SystemExit):
            ls.read_companies(self.leads, [("Colonne inconnue", "x")])

    def test_people_leads_qualifies_seulement(self):
        out = os.path.join(self.tmp, "p.csv")
        with mock.patch.object(ls, "search", return_value=PEOPLE_RESULTS) as search:
            ls.main(["--seen", self.seen, "people", "--input", self.leads, "--out", out, "--pause", "0",
                     "--decideurs-only", "--filtre", "Statut=qualifié", "--filtre", "Vague=1", "--titles", "CEO"])
        rows = read(out)
        self.assertEqual([r["nom"] for r in rows], ["Marie Martin", "Paul Durand"])
        self.assertTrue(all(r["site_web"] == "https://usinage-dupont.fr" for r in rows))
        self.assertEqual(search.call_count, 1)  # une entreprise, un seul paquet d'intitulés : une requête

    def test_liens_depuis_leads(self):
        out = os.path.join(self.tmp, "l.csv")
        ls.main(["--seen", self.seen, "liens", "--input", self.leads, "--out", out, "--filtre", "Statut=qualifié",
                 "--titles", "CEO"])
        rows = read(out)
        self.assertEqual([r["entreprise"] for r in rows], ["Usinage Dupont", "Autre Société"])
        self.assertEqual(rows[0]["page_linkedin_personnes"], "")  # pas de page LinkedIn connue : lien de recherche seulement
        self.assertIn("Usinage+Dupont", rows[0]["recherche_linkedin"])


class DropcontactTests(unittest.TestCase):
    def test_merge(self):
        row = {"nom": "Marie Martin", "statut": "valide"}
        ed.merge(row, {"first_name": "Marie", "last_name": "Martin",
                       "email": [{"email": "marie.martin@usinage-dupont.fr", "qualification": "nominative@pro"}],
                       "phone": "+33 4 50 00 00 00", "website": "usinage-dupont.fr"})
        self.assertEqual(row["email"], "marie.martin@usinage-dupont.fr")
        self.assertEqual(row["statut"], "enrichi")

    def test_split_name(self):
        self.assertEqual(ed.split_name("Jean de La Fontaine"), ("Jean", "de La Fontaine"))


if __name__ == "__main__":
    unittest.main()
