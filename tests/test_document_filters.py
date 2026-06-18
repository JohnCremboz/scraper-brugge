import unittest

from document_filters import (
    matches_blacklist,
    should_consider_scrape_input,
    should_keep_output_file,
)


class TestDocumentFilters(unittest.TestCase):
    def test_blacklisted_keywords_are_case_and_accent_insensitive(self):
        self.assertTrue(matches_blacklist("2026_Ordre du jour.pdf"))
        self.assertTrue(matches_blacklist("2026_RAPPORT.pdf"))
        self.assertTrue(matches_blacklist("2026_beheersovereenkomst.pdf"))

    def test_abbreviations_match_as_uppercase_tokens(self):
        self.assertTrue(matches_blacklist("2026_CBS_verslag.pdf"))
        self.assertTrue(matches_blacklist("MJP 2026.pdf"))
        self.assertFalse(matches_blacklist("abcbs-notulen.pdf"))

    def test_pst_meerjarenplan_fr_blacklisted(self):
        # Volledige naam
        self.assertTrue(matches_blacklist("programme-strategique-transversal-2024-2030.pdf"))
        self.assertTrue(matches_blacklist("002_Programme Strategique Transversal 2025-2030 - Prise d acte.pdf"))
        self.assertTrue(matches_blacklist("plan-strategique-transversal-commune-de-celles.pdf"))
        # PST-afkorting
        self.assertTrue(matches_blacklist("pst-2025-2030.pdf"))
        self.assertTrue(matches_blacklist("evaluation-pst.pdf"))
        self.assertTrue(matches_blacklist("019_Programme Strategique Transversal (PST) - Evaluation.pdf"))

    def test_rup_grup_prup_are_blacklisted(self):
        self.assertTrue(matches_blacklist("2026-03-09_RUP DB Fourage.pdf"))
        self.assertTrue(matches_blacklist("Gemeentelijk RUP Raversijde.pdf"))
        self.assertTrue(matches_blacklist("herziening ontwerp-RUP.pdf"))
        self.assertTrue(matches_blacklist("PRUP-Bruggenbeemd aankoop.pdf"))
        # Grupont (plaatsnaam): GRUP-afkorting matcht niet, maar "Compte 2023" wél (financieel)
        self.assertTrue(matches_blacklist("Eglise Saint-Denis de Grupont - Compte 2023.pdf"))
        # Zonder "compte" → GRUP-afkorting matcht niet → bewaard
        self.assertFalse(matches_blacklist("ZIT GRUPONT aankoop.pdf"))
        # Rupture is geen RUP-afkorting, maar wordt wél gefilterd via "bail"
        self.assertTrue(matches_blacklist("Rupture de Bail Emphyteotique.pdf"))

    def test_compound_blacklist_filters_goedkeuring_combinations(self):
        self.assertTrue(matches_blacklist("2025_GR_00054_Goedkeuring van de ontwerpakte voor innames.pdf"))
        self.assertTrue(matches_blacklist("Ontwerpakte wegenisproject Morkhovenseweg inname 8 goedkeuring.pdf"))
        self.assertTrue(matches_blacklist("Goedkeuring omgevingsvergunning industrieterrein.pdf"))
        # Goedkeuring alleen is niet genoeg om te filteren
        self.assertFalse(matches_blacklist("Goedkeuring notulen gemeenteraad.pdf"))
        self.assertFalse(matches_blacklist("2025_GR_00018_Creat Services - aanduiding vertegenwoordigers - Goedkeuring.pdf"))

    def test_compound_blacklist_filters_approbation_combinations(self):
        # Kerkfabriekrekeningen
        self.assertTrue(matches_blacklist("008_Tutelle speciale d approbation - Fabrique d eglise Saint Medard.pdf"))
        self.assertTrue(matches_blacklist("004_Fabrique d eglise Saint Remy - comptes exercice 2025 - Approbation.pdf"))
        # Jaarrekeningen
        self.assertTrue(matches_blacklist("003_Finances - AC - Compte de l exercice 2024 - Approbation.pdf"))
        self.assertTrue(matches_blacklist("CPAS - COMPTES - EXERCICE 2023 - POUR APPROBATION.pdf"))
        # Tutelle spéciale
        self.assertTrue(matches_blacklist("024_Finances - Tutelle speciale d approbation - CPAS - Modification budgetaire.pdf"))
        # Dotaties, leveringen, retributies, bestekken
        self.assertTrue(matches_blacklist("011_Finances - Zone de secours - Dotation 2025 - Approbation.pdf"))
        self.assertTrue(matches_blacklist("034_Fourniture et pose de chassis - Approbation des conditions.pdf"))
        self.assertTrue(matches_blacklist("004_Redevance location studios - Approbation.pdf"))
        self.assertTrue(matches_blacklist("023_Marche public - approbation du cahier des charges.pdf"))
        # Approbation PV en mandate-docs blijven gespaard
        self.assertFalse(matches_blacklist("001_Approbation du proces-verbal de la seance precedente.pdf"))
        self.assertFalse(matches_blacklist("058_Operateur de transport - Representation 2024-2030 - Approbation.pdf"))
        # Whitelist beschermt delegation + exercice
        self.assertFalse(matches_blacklist("013_Exercice des mandats - approbation.pdf"))

    def test_whitelist_protects_mandate_documents(self):
        self.assertFalse(matches_blacklist("Vaststelling mandaat algemene vergadering.pdf"))
        self.assertFalse(matches_blacklist("Aanduiding vertegenwoordiger intercommunale.pdf"))
        self.assertFalse(matches_blacklist("Goedkeuring afgevaardigde gemeenteraad.pdf"))
        # FR: délégation-documenten beschermd ondanks "concession" in naam
        self.assertFalse(matches_blacklist("DELEGATION DES COMPETENCES DU CONSEIL - MARCHES PUBLICS ET CONCESSIONS.pdf"))
        self.assertFalse(matches_blacklist("Delegation au College communal du pouvoir d'octroyer des concessions de sepultures.pdf"))
        self.assertFalse(matches_blacklist("005_Concessions funeraires - delegation du conseil communal.pdf"))

    def test_toestemming_concessie_bail_blacklisted(self):
        # toestemming: nutsinfrastructuur
        self.assertTrue(matches_blacklist("verlenen van de toestemming voor het uitbreiden van het glasvezelnetwerk.pdf"))
        self.assertTrue(matches_blacklist("Cameratoezicht - Principiele toestemming 2025.pdf"))
        # concessie NL: patrimonium
        self.assertTrue(matches_blacklist("Grafconcessie begraafplaats 2025.pdf"))
        self.assertTrue(matches_blacklist("Concessie horeca sporthal - gunning.pdf"))
        # concession FR: patrimonium
        self.assertTrue(matches_blacklist("Concession de domaine public pour installation kiosque.pdf"))
        self.assertTrue(matches_blacklist("Redevance concessions sepultures cimetieres 2026.pdf"))
        # bail FR: huurcontracten
        self.assertTrue(matches_blacklist("Bail emphyteotique ORES local technique.pdf"))
        self.assertTrue(matches_blacklist("Bail a ferme biens communaux - approbation contrat.pdf"))

    def test_prefixes_and_substrings_match(self):
        self.assertTrue(matches_blacklist("SP_2026_document.pdf"))
        self.assertTrue(matches_blacklist("WW document.pdf"))
        self.assertTrue(matches_blacklist("GRC2026.pdf"))
        self.assertTrue(matches_blacklist("2026_AR_document.pdf"))

    def test_bekendmaking_without_notulen_or_zittingsverslag_is_blacklisted(self):
        self.assertTrue(matches_blacklist("bekendmaking-gemeenteraad.pdf"))
        self.assertFalse(matches_blacklist("bekendmaking-notulen-gemeenteraad.pdf"))
        self.assertFalse(matches_blacklist("bekendmaking-zittingsverslag.pdf"))

    def test_motion_blacklisted_promotion_bewaard(self):
        # Politieke moties → filteren
        self.assertTrue(matches_blacklist("Motion du 09_02_2026.pdf"))
        self.assertTrue(matches_blacklist("054_Motion sur l urgence d une action pour la Palestine.pdf"))
        self.assertTrue(matches_blacklist("010_Motion de soutien aux agriculteurs.pdf"))
        self.assertTrue(matches_blacklist("002_Motion Anti Fasciste - Approbation.pdf"))
        # Promotion (APE, gezondheidsbevordering) → bewaren
        self.assertFalse(matches_blacklist("010_Aide a la Promotion de l Emploi - Fin de cession.pdf"))
        self.assertFalse(matches_blacklist("038_Representation de la Ville - ASBL Centre Local de Promotion de la Sante.pdf"))

    def test_batiment_blacklisted(self):
        self.assertTrue(matches_blacklist("015_BATIMENTS COMMUNAUX - Renovation du batiment et de ses abords.pdf"))
        self.assertTrue(matches_blacklist("016_Batiments communaux - installation nouveau systeme alarme incendie.pdf"))
        self.assertTrue(matches_blacklist("005_PROGRAMMATION FEDER - Renovation energetique des batiments.pdf"))

    def test_marche_public_blacklisted_delegation_bewaard(self):
        # Overheidsopdrachten → filteren
        self.assertTrue(matches_blacklist("019_Marches publics - Refection de voiries - Approbation des conditions.pdf"))
        self.assertTrue(matches_blacklist("014_Marche de fournitures - Acquisition de camions.pdf"))
        self.assertTrue(matches_blacklist("026_Zone de police - Marche conjoint fourniture de gasoil.pdf"))
        self.assertTrue(matches_blacklist("012_Marche d emprunt 2025 - Approbation des conditions.pdf"))
        # Délégation bevoegdheden + mandataires → beschermd door whitelist
        self.assertFalse(matches_blacklist("011_Delegation de competences en matiere de marches publics.pdf"))
        self.assertFalse(matches_blacklist("039_Mandataires - ASBL Art et Lettres en Marche - Assemblee generale - Delegues.pdf"))

    def test_convention_blacklisted_delegation_bewaard(self):
        # Operationele conventies → filteren
        self.assertTrue(matches_blacklist("008_Convention de partenariat Contrat de Riviere Senne 2026-2028.pdf"))
        self.assertTrue(matches_blacklist("014_Convention de mise a disposition locaux sportifs.pdf"))
        self.assertTrue(matches_blacklist("017_Convention de collaboration avec l ASBL Centre Culturel.pdf"))
        self.assertTrue(matches_blacklist("convention-intradel.pdf"))
        # Délégation/représentant → beschermd door whitelist
        self.assertFalse(matches_blacklist("008_Delegation au College communal pour la signature des conventions TEC.pdf"))
        self.assertFalse(matches_blacklist("004_CRECCIDE - Affiliation 2026 - Convention de partenariat et designation representant.pdf"))

    def test_contrat_blacklisted_designation_bewaard(self):
        # Beheers-/dienstencontracten → filteren
        self.assertTrue(matches_blacklist("010_Contrat de gestion entre la Ville et la RCA 2025-2027.pdf"))
        self.assertTrue(matches_blacklist("008_Contrat de Riviere Senne - Convention de partenariat 2026-2028.pdf"))
        self.assertTrue(matches_blacklist("013_Contrat de riviere Ourthe - Programme d actions 2026-2028 - Approbation.pdf"))
        self.assertTrue(matches_blacklist("019_Assurance hospitalisation collective - Contrat-cadre adhesion.pdf"))
        # Désignation représentant → beschermd door whitelist
        self.assertFalse(matches_blacklist("035_ASBL Contrat de Riviere Moselle - Designation representant.pdf"))
        self.assertFalse(matches_blacklist("020_Contrat de riviere Haute-Meuse - Designation des representants communaux.pdf"))

    def test_entretien_blacklisted(self):
        self.assertTrue(matches_blacklist("Entretien des voiries 2025 - Approbation des conditions.pdf"))
        self.assertTrue(matches_blacklist("006_Contrats d entretien des vehicules - modification.pdf"))
        self.assertTrue(matches_blacklist("Fourniture et entretien de tapis anti-poussieres.pdf"))

    def test_comptes_financieel_blacklisted_compterendu_bewaard(self):
        # Financiële rekeningen → filteren
        self.assertTrue(matches_blacklist("Compte de la Fabrique d eglise Saint-Medard 2024.pdf"))
        self.assertTrue(matches_blacklist("CPAS - Comptes 2024 - Approbation.pdf"))
        self.assertTrue(matches_blacklist("002_Comptes - Exercice 2024.pdf"))
        self.assertTrue(matches_blacklist("029_Arret des comptes de l exercice 2025 de la Ville de Liege.pdf"))
        self.assertTrue(matches_blacklist("007_ZONE DE SECOURS HAINAUT-EST - COMPTES 2023- POUR PRISE D ACTE.pdf"))
        self.assertTrue(matches_blacklist("008_ASBL Ligue Humaniste - Compte 2023.pdf"))
        self.assertTrue(matches_blacklist("compte-de-la-fabrique-deglise-saint-servais-pour-lexercice-2024.pdf"))
        # Compte-rendu (vergaderverslagen) → bewaren
        self.assertFalse(matches_blacklist("Compte rendu 01.12.2024.pdf"))
        self.assertFalse(matches_blacklist("Compte rendu CC 10.06.2025.pdf"))
        self.assertFalse(matches_blacklist("compte-rendu-seance-conseil-communal-2025.pdf"))

    def test_nomination_designation_whitelist(self):
        # Nomination en désignation beschermen ook in compte-context
        self.assertFalse(matches_blacklist("REGIE COMMUNALE - COMMISSAIRES AUX COMPTES - DESIGNATION DES MEMBRES.pdf"))
        self.assertFalse(matches_blacklist("Nomination des administrateurs - Compte rendu.pdf"))
        self.assertFalse(matches_blacklist("Designation du representant au conseil.pdf"))
        self.assertFalse(matches_blacklist("Désignation des délégués aux assemblées générales.pdf"))

    def test_extension_filter_supports_cleanup_and_pdf_only_input(self):
        self.assertTrue(should_keep_output_file("metadata.json"))
        self.assertTrue(should_keep_output_file("notulen.html"))
        self.assertFalse(should_keep_output_file("thumbnail.png"))

        self.assertTrue(should_consider_scrape_input("notulen.pdf"))
        self.assertFalse(should_consider_scrape_input("notulen.docx"))
        self.assertFalse(should_consider_scrape_input("agenda.pdf"))


if __name__ == "__main__":
    unittest.main()
