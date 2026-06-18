<#
.SYNOPSIS
    Blacklist-based cleaner for the pdfs/ scraper output directory.
.EXAMPLE
    .\clean_output.ps1              # dry run (veilig, toont wat verwijderd zou worden)
    .\clean_output.ps1 -Delete      # effectief verwijderen
    .\clean_output.ps1 -Delete -LogFile removed.csv
#>

param(
    [switch]$Delete,
    [string]$Root = "",
    [string]$LogFile = ""
)

# ---------------------------------------------------------------------------
# BLACKLIST - extensies die verwijderd worden
# ---------------------------------------------------------------------------
$Blacklist = @(
    ".wav"     # audio opnames
    ".mp3"   # audio opnames (reeds verwijderd)
    # ".html"  # scrapepagina's zonder documentwaarde
    # ".json"  # scraper metadata
    # ".zip"   # archieven
    # ".docx"  # Word-documenten — bewust uitgezet: iDélibé-gemeenten leveren notes de synthèse als DOCX
    ".msg"
    ".jpg"
    ".jpeg"
    ".bin"
    ".xlsx"
    ".png"
)

# Naampatronen (hoofdletterongevoelig, wildcards toegestaan)
$BlacklistPatterns = @(
    "*meerjarenplan*"                  # meerjarenplannen (NL)
    "*strategique transversal*"        # Programme/Plan Stratégique Transversal (FR: MJP-equivalent)
    "*stratégique transversal*"        # idem met accent
    "*PST*"                            # PST-afkorting (Programme Stratégique Transversal)
    "*materiel roulant*"               # FR: voertuigen/rollend materieel
    "*matériel roulant*"               # idem met accent
    "*dotation*"                       # FR: gemeentelijke bijdrage aan politie-/brandweerzone, CPAS
    "*entretien*"                      # FR: onderhoudsopdrachten (wegen, gebouwen, voertuigen, riolen)
    "*contrat*"                        # FR: beheers-/huur-/dienstencontracten (designation/représentant beschermd via whitelist)
    "*convention*"                     # FR: samenwerkings-/bezettings-/dienstenconventies (delegation/représentant beschermd via whitelist)
    "*marche public*"                  # FR: overheidsopdrachten
    "*marché public*"                  # idem met accent
    "*marches publics*"                # FR meervoud
    "*marchés publics*"                # idem met accent
    "*marche conjoint*"                # FR: gezamenlijke overheidsopdracht
    "*marché conjoint*"                # idem met accent
    "*batiment*"                       # FR: patrimonium/gebouwbeheer (bâtiment communal/public)
    "*bâtiment*"                       # idem met accent
    "*cimetiere*"                      # FR: kerkhofbeheer (grafconcessies, hernemingen)
    "*cimetière*"                      # idem met accent
    "*reparation*"                     # FR: herstellingen (wegen, daken, voertuigen)
    "*réparation*"                     # idem met accent
    "*etude*"                          # FR: technische/infrastructurele studies
    "*étude*"                          # idem met accent
    "*dechet*"                         # FR: afvalbeheer (tarieven, collecte, coût-vérité)
    "*déchet*"                         # idem met accent
    "*personnel*"                      # FR: personeelsadministratie (statuten, barema's, aanwervingen)
    "*fabrique*"                       # FR: kerkfabrieken (tutelle, budgetten, subsidies)
    "*delinquance*"                    # FR: milieu-overtredingen, administratieve sancties
    "*délinquance*"                    # idem met accent
    "*domaine public*"                 # FR: openbaar domein (innames, redevances, overdrachten)
    "*patrimoine*"                     # FR: patrimonium (verkopen, aankopen, overdrachten)
    "*bilan*"                          # FR: balansen, activiteitsverslagen, jaarrekeningen
    "*avantage*"                       # FR: sociale voordelen (scholen, personeel)
    "*urbanisme*"                      # FR: ruimtelijke ordening (vergunningen, schema's)
    "*amenagement*"                    # FR: inrichting/aménagement du territoire
    "*aménagement*"                    # idem met accent
    "*acquisition*"                    # FR: aankopen (voertuigen, machines, percelen)
    "*immersion*"                      # FR: taalimmersie-onderwijs + overstromingszones (ZIT)
    "*subside*"                        # FR: subsidies aan vzw's/sport/cultuur
    "*subvention*"                     # FR: idem (alternatieve schrijfwijze)
    "*redevance*"                      # FR: gemeentelijke heffingen (zalen, maaltijden, begraafplaatsen)
    "*tax on pylons*"                  # Waals programma GSM-masten (TOP III) — financieel/infrastructuur
    "*arrete*"                         # FR: arrêtés du bourgmestre/de police/royaux/ministériels (burgemeestersbesluiten, noodmaatregelen)
    "*arrêté*"                         # idem met accent
    "*population*"                     # FR: bevolkingsstatistieken, schoolpopulatie, bevolkingsregisterdienst
    "*interpellation citoyenne*"       # FR: burgervragen aan de gemeenteraad — geen mandaatbeslissing
    "*sanctionerend ambtenaar*"        # NL: GAS-handhaving — interne aanstelling, geen extern mandaat
    "*fonctionnaire sanctionnateur*"   # FR: idem (m)
    "*fonctionnaire sanctionnatrice*"  # FR: idem (f)
    "*sterilisatie*"                   # NL: dierenwelzijn/medisch — nooit mandaatrelevant
    "*sterilisation*"                  # FR: idem (zonder accent)
    "*stérilisation*"                  # FR: idem met accent
    "*handycity*"                      # inclusie-charter personen met handicap
    "*auditcharter*"                   # intern auditframework
    "*charte IA*"                      # AI-gebruiksbeleid
    "*charte informatique*"            # IT-gebruiksbeleid
    "*charte de déontologie*"          # deontologiecharter gemeenteraad
    "*charte de deontologie*"          # idem zonder accent
    "*charte PEFC*"                    # bosbeheercertificering
    "*charte de nourrissage*"          # katten voederbeleid
    "*intelligence artificielle*"      # AI-charters en -beleid
    "*droit de chasse*"                # FR: jachtrecht — patrimonium
    "*location de chasse*"             # FR: jachtverhuur
    "*location des chasses*"           # FR: idem meervoud
    "*baux de chasse*"                 # FR: jachtpachten
    "*bail de chasse*"                 # FR: jachtpacht (enkelvoud)
    "*devis forestier*"                # FR: bosbeheercijfers/ramingen
    "*regime forestier*"               # FR: bosbeheersregime
    "*régime forestier*"               # FR: idem met accent
    "*parcelle forestiere*"            # FR: bosgrondperceel
    "*parcelle forestière*"            # FR: idem met accent
    "*parcelles forestieres*"          # FR: meervoud
    "*parcelles forestières*"          # FR: meervoud met accent
    "*chemin forestier*"               # FR: boswegen
    "*camera*"                         # NL/FR: bewakingscamera's, ANPR, cameratoezicht — ordehandhaving
    "*caméra*"                         # FR: idem met accent
    "*kerkfabriek*"                    # NL: kerkfabrieken — pendant van FR *fabrique*
    "*technische dienst*"              # NL: operationele beslissingen technische dienst
    "*financiele dienst*"              # NL: financiële dienst — budgetten, leningen, belastingen
    "*financiële dienst*"              # NL: idem met accent
    "*vrije tijd*"                     # NL: vrijetijdsdienst — commissie/samenstelling beschermd via whitelist
    "*circulation*"                    # FR: verkeersreglementering — designation/commissie beschermd via whitelist
    "*braderie*"                       # NL/FR: koopjesmarkt/braderie — nooit mandaatrelevant
    "*feest*"                          # NL: feesten — samenstelling/aanstelling beschermd via whitelist
    "*fete*"                           # FR: fête (zonder accent) — designation/demission beschermd via whitelist
    "*fête*"                           # FR: fête (met accent)
    "*viering*"                        # NL: vieringen, huldigingen — nooit mandaatrelevant
    "*principebeslissing*"             # NL: principebeslissingen — samenwerkingsverbanden beschermd via whitelist
    "*modification budgetaire*"        # FR: begrotingswijzigingen — puur financieel
    "*modification budgétaire*"        # FR: idem met accent
    "*modifications budgetaires*"      # FR: meervoud
    "*modifications budgétaires*"      # FR: meervoud met accent
    "motion *"                         # FR: politieke motie (prefix) — niet "promotion"
    "* motion *"                       # FR: motie midden in naam
    "* motion"                         # FR: motie op einde naam
    "*reglement*"        # reglementen (NL/DE)
    "*règlement*"        # reglementen (FR)
    "*jaarrekening*"                   # jaarrekeningen (NL)
    "*comptes annuels*"                # jaarrekeningen (FR)
    "*jahresrechnung*"                 # jaarrekeningen (DE)
    "*opcentiemen*"                    # NL: belastingaanslagvoeten onroerende voorheffing — puur fiscaal
    "*centimes additionnels*"          # FR: idem (centimes additionnels au précompte immobilier)
    "*retributie*"       # retributies
    "*signalisatie*"     # signalisatieplannen
    "*parkeer*"          # parkeerreglementen/-verordeningen
    "*kermis*"           # kermisvergunningen/-verordeningen
    "*kermesse*"         # kermesse (FR)
    "*vergunning*"       # alle soorten vergunningen (evenementen, horecavergunningen, stedenbouw)
    "*seingever*"        # seingevers bij wieler-/wandelevenementen
    "WW *"               # wielerwedstrijd-dossiers (prefix)
    "WW_*"               # wielerwedstrijd-dossiers (prefix met underscore)
    "*baan*"             # wegbenaming (Koninklijke Baan, rijbaan, ...)
    "*wegen*"            # weginfrastructuur (zaak van de wegen, wegen- en rioleringswerken)
    "*protocol*"         # samenwerkings-/GAS-/GDPR-protocollen (excl. Protokoll: Duits)
    "*dagorde*"          # dagorden
    "*agenda*"           # agenda-lijsten van vergaderingen (≠ agendapunt: zie whitelist)
    "*begroting*"        # begrotingen
    "*balans*"           # balansen
    "*bijlage*"          # bijlagen
    "*addendum*"         # addenda
    "*beleidsplan*"      # beleidsplannen
    "*actieplan*"        # actieplannen
    "*mobiliteitsplan*"  # mobiliteitsplannen
    "*samenwerkingsovereenkomst*"  # SWO
    "SP *"                         # signalisatieplannen (prefix "SP ")
    "SP_*"                         # signalisatieplannen (prefix "SP_")
    "*verordening*"         # tijdelijke verkeersbeperkingen
    "*omgevingsvergunning*"        # stedenbouwkundige vergunningen
    "TR_*"                         # verkeersregelingen (prefix "TR_")
    "TR *"                         # verkeersregelingen (prefix "TR ")
    "*MJP*"
    "*verkaveling*"
    "*rooilijn*"
    "*CBS*"
    "*budget*"
    "*BB*"
    "*RPR*"
    "*rechtspositie*"
    "*VB*"
    "*belasting*"        # belastingen (NL)
    "*taxe*"             # belastingen (FR)
    "*impôt*"            # belastingen (FR)
    "*steuer*"           # belastingen (DE)
    "*afspraken*"
    "*folder*"
    "*flyer*"
    "*overeenkomst*"
    "*besluitenlijst*"
    "*advies*"
    "*rapport*"
    "*kennisgeving*"
    "*omleiding*"
    "*melding*"
    "*foto*"
    "*bestek*"
    "*HHR*"
    "*jaarverslag*"
    "*signed*"
    "getekend"
    "*schatting*"
    "*fiche*"
    "*verklaring*"       # verklaringen (NL)
    "*déclaration*"      # verklaringen (FR)
    "*erklärung*"        # verklaringen (DE)
    "*aanvraag*"         # aanvragen (NL)
    "*demande*"          # aanvragen (FR)
    "*antrag*"           # aanvragen (DE)
    "*aanleg*"
    "*architect*"
    "*bw*"
    "*brief*"
    "*initiatief*"
    "*inplanting*"
    "*schets*"
    "*erfpacht*"
    "*opmeting*"
    "*mutatie*"
    "*petitie*"
    "*code*"
    "*erfgoed*"
    "*documentatie*"
    "*brochure*"
    "*nota*"
    "*straat*"
    # "*laan*" verwijderd: matcht "West-Vlaanderen", "Oost-Vlaanderen", "rioolaansluiting" → te breed
    # echte "laan"-besluiten worden al gevangen door *verordening* en *signalisatie*
    "*dreef*"
    "*plein*"
    "*markt*"
    "*premie*"
    "*consessie*"
    "*verkoop*"
    "*beheer*"
    "*JR*"
    "*delegatie*"
    "*convenant*"
    "*kaart*"
    "*carnaval*"
    "*festival*"
    "*wedstrijd*"
    "*verkeer*"
    "*ordonnance*"
    "*avis*"
    "*ordre du jour*"
    "*question*"
    "*vraag*"
    "*travaux*"
    "*werken*"
    "*octroi*"
    "*place*"
    "*rue*"
    "*boulevard*"
    "*comptes annuels*"  # jaarrekeningen FR (al aanwezig, redundant maar expliciet)
    "comptes *"          # comptes CPAS, comptes zone, comptes fabrique, … (prefix)
    "compte *"           # compte exercice, compte fabrique, … (prefix singulier)
    "* comptes *"        # midden in bestandsnaam
    "* compte *"         # midden in bestandsnaam singulier
    "* comptes"          # bestandsnaam eindigt op comptes
    "* compte"           # bestandsnaam eindigt op compte
)

# Whitelist - bestandsnamen met deze woorden worden NOOIT verwijderd,
# ook niet als ze op de blacklist matchen (extensie of patroon)
$WhitelistPatterns = @(
    # NL — bestaande termen
    "*aanduiding*"
    "*representant*"
    "*vertegenwoordiger*"
    # NL — aanvullende benoemingstermen
    "*benoeming*"          # benoeming bestuurder/lid/secretaris
    "*aanstelling*"        # aanstelling waarnemend burgemeester (valt anders onder *BB*)
    "*voordracht*"         # voordracht kandidaat-bestuurder AGB/intercommunale
    "*afvaardiging*"       # afvaardiging naar AV intercommunale
    "*mandataris*"         # mandataris (zeer specifiek)
    "*mandaat*"            # mandaat in bestandsnaam primeert altijd boven blacklist
    # FR — zonder accent (bestaand) en MET accent (nieuw)
    # PS -ilike is NIET accent-insensitief: "é" ≠ "e" → beide varianten nodig
    "*designation*"
    "*désignation*"        # FR met accent
    "*représentant*"       # FR met accent (≠ *representant*)
    "*nomination*"         # nomination administrateur/membre
    "*remplacement*"       # remplacement du représentant → bevat "place" → KRITISCH
    "*délégué*"            # délégué à l'AV intercommunale
    # Compte-rendu (vergaderverslagen) — worden NIET gefilterd ondanks *compte* blacklist
    "*compte-rendu*"
    "*compte rendu*"
    # Désignation / nomination — aanduiding commissarissen, bestuurders (beschermt ook comptes-context)
    "*nomination*"
    "*désignation*"
    "*designation*"
    # Intercommunale AV-beslissingen
    "*avis*intercommunale*"      # gemeentelijke standpuntname bij AV intercommunale
    "*assemblée générale*"       # FR: approbation OJ intercommunale AV (VIVALIA, IDELUX, ORES,...)
    "*commissie*"                # NL: raadscommissies — samenstelling altijd mandaatrelevant
    "*samenstelling*"            # NL: samenstelling van een raad/bestuur/commissie
    "*assemblee generale*"       # FR zonder accenten (fallback)
    "*algemene vergadering*"     # NL: goedkeuring agenda AV intercommunale
    "*agendapunt*"               # besprekingsdocumenten per agendapunt (≠ droge agenda-lijst)
)
# ---------------------------------------------------------------------------

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Root -eq "") { $Root = Join-Path $ScriptDir "pdfs" }

if (-not (Test-Path $Root)) {
    Write-Error "Map niet gevonden: $Root"
    exit 1
}

$mode = if ($Delete) { "VERWIJDEREN" } else { "DRY RUN" }
Write-Host ""
Write-Host "=== clean_output.ps1 [$mode] ===" -ForegroundColor Cyan
Write-Host "Root     : $Root"
Write-Host "Blacklist: $($Blacklist -join ', ')"
if ($BlacklistPatterns.Count -gt 0) {
    Write-Host "Patronen : $($BlacklistPatterns -join ', ')"
}
Write-Host ""

# Bestanden verzamelen met voortgangsindicator per gemeente
Write-Host "Scannen..." -ForegroundColor Cyan
$startScan = Get-Date
$hits = [System.Collections.Generic.List[System.IO.FileInfo]]::new()
$scanTeller = 0
$huidigGemeente = ""

Get-ChildItem $Root -Recurse -File | ForEach-Object {
    $scanTeller++

    # Toon nieuwe gemeente zodra we er een betreden
    $delen = $_.FullName -replace [regex]::Escape($Root + "\"), "" | Split-Path -Parent
    $gemeente = $delen.Split("\")[0]
    if ($gemeente -ne $huidigGemeente) {
        if ($huidigGemeente -ne "") {
            Write-Host ""
        }
        $huidigGemeente = $gemeente
        Write-Host ("  [{0}] {1,-40}" -f (Get-Date -Format "HH:mm:ss"), $gemeente) -NoNewline
    }

    # Whitelist-check - heeft voorrang op de blacklist
    $whitelisted = $false
    foreach ($pat in $WhitelistPatterns) {
        if ($_.Name -ilike $pat) { $whitelisted = $true; break }
    }

    # Blacklist-check
    $match = $false
    if (-not $whitelisted) {
        if ($Blacklist -contains $_.Extension.ToLower()) { $match = $true }
        if (-not $match) {
            foreach ($pat in $BlacklistPatterns) {
                if ($_.Name -ilike $pat) { $match = $true; break }
            }
        }
    }
    if ($match) { $hits.Add($_) }
}
Write-Host ""

$duurScan = [math]::Round(((Get-Date) - $startScan).TotalSeconds, 1)
Write-Host ("Scan klaar in {0}s - {1} bestanden gescand, {2} op blacklist." -f $duurScan, $scanTeller, $hits.Count) -ForegroundColor Cyan
Write-Host ""

if ($hits.Count -eq 0) {
    Write-Host "Geen bestanden gevonden die overeenkomen met de blacklist." -ForegroundColor Green
    exit 0
}

# Samenvatting per extensie
$byExt = $hits | Group-Object { $_.Extension.ToLower() } | Sort-Object Name
Write-Host "Gevonden bestanden:" -ForegroundColor Yellow
foreach ($g in $byExt) {
    $sizeMB = [math]::Round(($g.Group | Measure-Object Length -Sum).Sum / 1MB, 1)
    Write-Host ("  {0,-8} {1,5} bestanden   {2,8} MB" -f $g.Name, $g.Count, $sizeMB)
}
$totalMB = [math]::Round(($hits | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host ("  {0,-8} {1,5} bestanden   {2,8} MB  (totaal)" -f "TOTAAL", $hits.Count, $totalMB)
Write-Host ""

# Log-rijen aanmaken
$logRows = $hits | ForEach-Object {
    $rel = $_.FullName -replace [regex]::Escape($Root + "\"), ""
    $gemeente = $rel.Split("\")[0]
    [PSCustomObject]@{
        Gemeente   = $gemeente
        Extensie   = $_.Extension.ToLower()
        Bestand    = $_.Name
        Pad        = $_.FullName
        GrootteMB  = [math]::Round($_.Length / 1MB, 3)
        Verwijderd = $false
    }
}

if (-not $Delete) {
    Write-Host "Dry run - geen bestanden verwijderd." -ForegroundColor Yellow
    Write-Host "Voer opnieuw uit met -Delete om effectief te verwijderen."
} else {
    $deleted = 0
    $errors  = 0
    $totaal  = $hits.Count
    foreach ($f in $hits) {
        try {
            Remove-Item $f.FullName -Force
            ($logRows | Where-Object { $_.Pad -eq $f.FullName }).Verwijderd = $true
            $deleted++
            Write-Host ("[{0,6}/{1}] Verwijderd: {2}" -f $deleted, $totaal, $f.Name)
        } catch {
            $errors++
            Write-Warning ("[{0,6}/{1}] Fout: {2} - {3}" -f ($deleted + $errors), $totaal, $f.Name, $_)
        }
    }
    $summary = "$deleted bestanden verwijderd"
    if ($errors -gt 0) { $summary += ", $errors fouten" }
    Write-Host $summary -ForegroundColor Green
}

# Log wegschrijven indien gevraagd
if ($LogFile -ne "") {
    $logRows | Export-Csv -Path $LogFile -NoTypeInformation -Encoding UTF8
    Write-Host "Log geschreven naar: $LogFile"
}

Write-Host ""
