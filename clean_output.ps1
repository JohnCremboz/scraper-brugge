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
    # ".docx"  # Word-documenten
)

# Naampatronen (hoofdletterongevoelig, wildcards toegestaan)
$BlacklistPatterns = @(
    "*meerjarenplan*"    # meerjarenplannen
    "*reglement*"        # reglementen
    "*jaarrekening*"     # jaarrekeningen
    "*retributie*"       # retributies
    "*signalisatie*"     # signalisatieplannen
    "*dagorde*"          # dagorden
    "*begroting*"        # begrotingen
    "*balans*"           # balansen
    "*bijlage*"          # bijlagen
    "*addendum*"         # addenda
    "*beleidsplan*"      # beleidsplannen
    "*samenwerkingsovereenkomst*"  # SWO
    "SP *"                         # signalisatieplannen (prefix "SP ")
    "SP_*"                         # signalisatieplannen (prefix "SP_")
    "*politieverordening*"         # tijdelijke verkeersbeperkingen
    "*omgevingsvergunning*"        # stedenbouwkundige vergunningen
    "TR_*"                         # verkeersregelingen (prefix "TR_")
    "TR *"                         # verkeersregelingen (prefix "TR ")
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

# Bestanden verzamelen die overeenkomen met de blacklist
$hits = Get-ChildItem $Root -Recurse -File | Where-Object {
    if ($Blacklist -contains $_.Extension.ToLower()) { return $true }
    foreach ($pat in $BlacklistPatterns) {
        if ($_.Name -ilike $pat) { return $true }
    }
    return $false
}

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
    foreach ($f in $hits) {
        try {
            Remove-Item $f.FullName -Force
            ($logRows | Where-Object { $_.Pad -eq $f.FullName }).Verwijderd = $true
            $deleted++
        } catch {
            Write-Warning "Kon niet verwijderen: $($f.FullName)"
            $errors++
        }
    }
    $summary = "$deleted bestanden verwijderd"
    if ($errors -gt 0) { $summary += ", $errors fouten" }
    Write-Host "$summary." -ForegroundColor Green
}

# Log wegschrijven indien gevraagd
if ($LogFile -ne "") {
    $logRows | Export-Csv -Path $LogFile -NoTypeInformation -Encoding UTF8
    Write-Host "Log geschreven naar: $LogFile"
}

Write-Host ""
