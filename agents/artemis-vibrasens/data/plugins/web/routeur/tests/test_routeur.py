"""Tests du routeur web, sans réseau : chaque fournisseur est remplacé par un faux."""
import importlib.util
import sys
from pathlib import Path

import pytest

ICI = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('routeur_provider', ICI / 'provider.py')
provider = importlib.util.module_from_spec(spec)
sys.modules['routeur_provider'] = provider
spec.loader.exec_module(provider)
VendorError = provider.VendorError


class Etat:
    """Remplace ctx.state (stockage JSON du plugin)."""

    def __init__(self, dossier):
        self.data, self.data_dir = {}, dossier

    def get(self, k, defaut=None):
        return self.data.get(k, defaut)

    def set(self, k, v):
        self.data[k] = v


class Ctx:
    def __init__(self, dossier, **reglages):
        self.state, self.reglages = Etat(dossier), reglages

    def get_config(self, k):
        return self.reglages.get(k)


def lignes(*titres):
    return [provider._row(f'https://site{i}.fr/', t, t, i + 1) for i, t in enumerate(titres)]


@pytest.fixture
def faux(monkeypatch):
    """Fournisseurs factices : faux[nom] = fonction ou exception à lever."""
    comportements = {}

    def fabrique(nom, op):
        def appel(*args):
            c = comportements[(nom, op)]
            if isinstance(c, Exception):
                raise c
            return c(*args) if callable(c) else c
        return appel

    vendeurs = {n: {'key': '', 'search': fabrique(n, 'search'), 'fetch': fabrique(n, 'fetch')}
                for n in ('a', 'b', 'c', 'payant')}
    monkeypatch.setattr(provider, 'VENDORS', vendeurs)
    monkeypatch.setattr(provider, 'COST_USD', {'payant.search': 0.001})
    return comportements


def routeur(tmp_path, **reglages):
    reglages.setdefault('search_chain', ['a', 'b', 'c', 'payant'])
    reglages.setdefault('extract_chain', ['a', 'b', 'c'])
    reglages.setdefault('daily_budget', {})
    return provider.RouteurWebProvider(Ctx(tmp_path, **reglages))


def test_premier_moteur_qui_repond_juste(tmp_path, faux):
    faux[('a', 'search')] = lignes('Taux de cotisation URSSAF 2026')
    r = routeur(tmp_path).search('taux cotisation urssaf 2026')
    assert r['success'] and r['data']['served_by'] == 'a'


def test_vide_puis_saturation_puis_hors_sujet_puis_payant(tmp_path, faux):
    faux[('a', 'search')] = []
    faux[('b', 'search')] = VendorError('rate_limit', 'HTTP 429')
    faux[('c', 'search')] = lignes('Recette de gyudon', 'Météo à Lyon')
    faux[('payant', 'search')] = lignes('Taux de TVA réduit restauration 2026')
    r = routeur(tmp_path).search('taux TVA restauration 2026')
    assert r['data']['served_by'] == 'payant'
    assert [t.split(':')[0] for t in r['data']['tried']] == ['a', 'b', 'c', 'payant']
    assert 'rate_limit' in r['data']['tried'][1] and r['data']['tried'][2] == 'c:off_topic'


def test_tout_hors_sujet_rend_le_premier_avec_un_avertissement(tmp_path, faux):
    for n in ('a', 'b', 'c', 'payant'):
        faux[(n, 'search')] = lignes('Sans rapport')
    r = routeur(tmp_path).search('déclaration TVA trimestrielle')
    assert r['success'] and r['data']['served_by'] == 'a' and 'warning' in r['data']


def test_tout_en_erreur(tmp_path, faux):
    for n in ('a', 'b', 'c', 'payant'):
        faux[(n, 'search')] = VendorError('server', 'HTTP 503')
    r = routeur(tmp_path).search('urssaf')
    assert r['success'] is False and 'a:server' in r['error']


def test_un_moteur_sature_est_mis_en_pause(tmp_path, faux):
    faux[('a', 'search')] = VendorError('rate_limit', 'HTTP 429')
    faux[('b', 'search')] = lignes('URSSAF cotisations')
    rt = routeur(tmp_path)
    rt.search('urssaf cotisations')
    faux[('a', 'search')] = lignes('URSSAF cotisations')  # rétabli, mais encore en pause
    r = rt.search('urssaf cotisations')
    assert r['data']['tried'][0] == 'a:cooldown' and r['data']['served_by'] == 'b'


def test_budget_du_jour_respecte(tmp_path, faux):
    faux[('a', 'search')] = []
    faux[('payant', 'search')] = lignes('Impots gouv TVA')
    faux[('b', 'search')] = []
    faux[('c', 'search')] = []
    rt = routeur(tmp_path, daily_budget={'payant.search': 1})
    assert rt.search('impots tva')['data']['served_by'] == 'payant'
    r = rt.search('impots tva')
    assert r['success'] is False and 'payant:budget' in r['error']


def test_journal_une_ligne_par_tentative(tmp_path, faux):
    faux[('a', 'search')] = []
    faux[('b', 'search')] = lignes('Legifrance article 287 CGI')
    routeur(tmp_path).search('article 287 CGI legifrance')
    journal = (tmp_path / 'calls.jsonl').read_text().splitlines()
    assert len(journal) == 2 and '"vendor": "b"' in journal[1] and '"outcome": "ok"' in journal[1]


def test_lecture_page_de_blocage_puis_page_courte_puis_bonne(tmp_path, faux):
    faux[('a', 'fetch')] = ('Just a moment...', 'Checking your browser before accessing', 'u')
    faux[('b', 'fetch')] = ('Titre', 'trop court', 'u')
    faux[('c', 'fetch')] = ('Guide TVA', 'texte utile ' * 100, 'u')
    doc = routeur(tmp_path).extract(['https://impots.gouv.fr/guide'])[0]
    assert doc['metadata']['served_by'] == 'c' and 'error' not in doc
    assert doc['metadata']['tried'] == ['a:blocked', 'b:short', 'c:ok']


def test_lecture_impossible_renvoie_vers_le_navigateur(tmp_path, faux):
    for n in ('a', 'b', 'c'):
        faux[(n, 'fetch')] = VendorError('client', 'HTTP 403')
    doc = routeur(tmp_path).extract(['https://exemple.fr'])[0]
    assert 'browser_navigate' in doc['error'] and doc['content'] == ''


def test_x_com_jamais_chez_le_fournisseur_interdit(tmp_path, faux):
    faux[('a', 'fetch')] = ('Post', 'contenu ' * 100, 'u')
    doc = routeur(tmp_path, deny={'a': ['x.com']}).extract(['https://x.com/nico/status/1'])[0]
    assert doc['metadata']['tried'][0] == 'a:deny'


def test_sans_cle_le_fournisseur_est_saute(tmp_path, faux, monkeypatch):
    provider.VENDORS['a']['key'] = 'CLE_ABSENTE_POUR_LE_TEST'
    monkeypatch.delenv('CLE_ABSENTE_POUR_LE_TEST', raising=False)
    faux[('b', 'search')] = lignes('URSSAF')
    r = routeur(tmp_path).search('urssaf')
    assert r['data']['served_by'] == 'b' and not any(t.startswith('a:') for t in r['data']['tried'])


def test_pertinence():
    assert provider.overlap('taux de TVA réduit', lignes('Taux de TVA réduit en France')) == 1.0
    assert provider.overlap('site:urssaf.fr cotisations', lignes('Les cotisations sociales')) == 1.0
    assert provider.overlap('gyudon recette', lignes('Météo')) == 0.0


def test_lecture_du_texte_exa():
    texte = ("Title: Taux de cotisations 2026\nURL: https://www.urssaf.fr/taux\nPublished: 2026-01-02\n"
             "Highlights:\nLe taux de la cotisation…\n---\nTitle: Autre\nURL: https://exemple.fr/\nHighlights:\nRien")
    rows = provider._exa_rows(texte, 5)
    assert [r['url'] for r in rows] == ['https://www.urssaf.fr/taux', 'https://exemple.fr/']
    assert rows[0]['description'].startswith('Le taux') and rows[1]['position'] == 2


REFUS_EXA = ("You've hit Exa's free MCP rate limit. To continue using without limits, create your own Exa API key.")


def test_refus_exa_reessaye_une_fois(monkeypatch):
    reponses = [REFUS_EXA, "Title: Taux URSSAF\nURL: https://www.urssaf.fr/taux\nHighlights:\nTaux"]
    monkeypatch.setattr(provider, '_mcp', lambda *a: reponses.pop(0))
    monkeypatch.setattr(provider, 'EXA_PAUSE_S', 0)
    assert [r['url'] for r in provider.exa_free_search('taux urssaf', 5, 1)] == ['https://www.urssaf.fr/taux']


def test_refus_exa_persistant_est_une_saturation(monkeypatch):
    monkeypatch.setattr(provider, '_mcp', lambda *a: REFUS_EXA)
    monkeypatch.setattr(provider, 'EXA_PAUSE_S', 0)
    with pytest.raises(VendorError) as e:
        provider.exa_free_search('taux urssaf', 5, 1)
    assert e.value.kind == 'rate_limit'


def test_flux_youtube_lu_d_abord_en_direct(tmp_path, faux, monkeypatch):
    flux = '<feed><entry><title>Vidéo du jour</title></entry></feed>' * 20
    monkeypatch.setitem(provider.VENDORS, 'direct', {'key': '', 'fetch': lambda u, t: ('', flux, u)})
    faux[('a', 'fetch')] = ('Copie ancienne', 'x' * 500, 'https://www.youtube.com/feeds/videos.xml?channel_id=UC1')
    r = provider.RouteurWebProvider(Ctx(tmp_path, extract_chain=['a'], direct_first=['youtube.com/feeds/']))
    doc = r.extract(['https://www.youtube.com/feeds/videos.xml?channel_id=UC1'])[0]
    assert doc['metadata']['served_by'] == 'direct' and doc['content'] == flux
    autre = r.extract(['https://site.fr/page'])[0]
    assert autre['metadata']['served_by'] == 'a'
