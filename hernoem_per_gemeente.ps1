# hernoem_per_gemeente.ps1
# Voert hernoem_besluiten.py uit per gemeente-submap.
# Een crash/segfault in PyMuPDF breekt enkel die gemeente af.
#
# Gebruik:
#   .\hernoem_per_gemeente.ps1              # dry run
#   .\hernoem_per_gemeente.ps1 -Rename      # effectief hernoemen
#   .\hernoem_per_gemeente.ps1 -Rename -Log hernoem_totaal.csv

param(
    [switch]$Rename,
    [string]$Root = "pdfs",
    [string]$Log = ""
)

$gemeenten = Get-ChildItem $Root -Directory | Sort-Object Name
$totaalGemeenten = $gemeenten.Count
$teller = 0
$totaalHernoemd = 0
$totaalGeenTitel = 0
$totaalFouten = 0
$crashGemeenten = @()

foreach ($g in $gemeenten) {
    $teller++
    $naam = $g.Name
    Write-Host ("[{0,3}/{1}] {2,-40}" -f $teller, $totaalGemeenten, $naam) -NoNewline

    $argList = @("hernoem_besluiten.py", "--gemeente", $naam, "--root", $Root)
    if ($Rename) { $argList += "--rename" }
    if ($Log -ne "") { $argList += @("--log", "${naam}_hernoem.csv") }

    try {
        $output = & uv run python @argList 2>&1
        $exitCode = $LASTEXITCODE

        if ($exitCode -ne 0) {
            Write-Host " [CRASH exit=$exitCode]" -ForegroundColor Red
            $crashGemeenten += $naam
            continue
        }

        $hernoemd = 0; $geenTitel = 0; $fouten = 0
        foreach ($line in $output) {
            if ($line -match "Hernoemd\s*:\s*([\d,]+)")    { $hernoemd   = [int]($Matches[1] -replace ",","") }
            if ($line -match "Geen titel\s*:\s*([\d,]+)")  { $geenTitel  = [int]($Matches[1] -replace ",","") }
            if ($line -match "Fouten\s*:\s*([\d,]+)")      { $fouten     = [int]($Matches[1] -replace ",","") }
        }
        $totaalHernoemd   += $hernoemd
        $totaalGeenTitel  += $geenTitel
        $totaalFouten     += $fouten

        $modus = if ($Rename) { "hernoemd=$hernoemd" } else { "te hernoemen=$hernoemd" }
        Write-Host (" OK ($modus, geen_titel=$geenTitel)")

    } catch {
        Write-Host " [FOUT: $_]" -ForegroundColor Red
        $crashGemeenten += $naam
    }
}

$modus = if ($Rename) { "HERNOEMD" } else { "DRY RUN" }
Write-Host ""
Write-Host "=== Totaal [$modus] ==="
Write-Host ("  Gemeenten  : {0,6}" -f ($totaalGemeenten - $crashGemeenten.Count))
if ($Rename) {
    Write-Host ("  Hernoemd   : {0,6:N0}" -f $totaalHernoemd)
} else {
    Write-Host ("  Te hernoemen: {0,5:N0}" -f $totaalHernoemd)
}
Write-Host ("  Geen titel : {0,6:N0}" -f $totaalGeenTitel)
if ($totaalFouten -gt 0) { Write-Host ("  Fouten     : {0,6:N0}" -f $totaalFouten) -ForegroundColor Yellow }
if ($crashGemeenten.Count -gt 0) {
    Write-Host ("  Crashes ({0}): {1}" -f $crashGemeenten.Count, ($crashGemeenten -join ", ")) -ForegroundColor Red
}
