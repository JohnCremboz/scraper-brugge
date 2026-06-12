# filter_per_gemeente.ps1
# Voert filter_inhoud.py uit per gemeente-submap.
# Een segfault in PyMuPDF bij een corrupte PDF breekt alleen die gemeente af,
# de rest loopt gewoon door.
#
# Gebruik:
#   .\filter_per_gemeente.ps1              # dry run
#   .\filter_per_gemeente.ps1 -Delete      # verwijder irrelevante PDFs
#   .\filter_per_gemeente.ps1 -Root "pdfs" -Delete

param(
    [switch]$Delete,
    [string]$Root = "pdfs"
)

$gemeenten = Get-ChildItem $Root -Directory | Sort-Object Name
$totaalGemeenten = $gemeenten.Count
$teller = 0

$totaalPDFs      = 0
$totaalBewaard   = 0
$totaalGescand   = 0
$totaalVerwijderd = 0
$totaalMB        = 0.0
$fouten          = @()

foreach ($g in $gemeenten) {
    $teller++
    $naam = $g.Name
    Write-Host ("[{0,3}/{1}] {2,-40}" -f $teller, $totaalGemeenten, $naam) -NoNewline

    $args_list = @("filter_inhoud.py", "--gemeente", $naam, "--root", $Root, "--workers", "1")
    if ($Delete) { $args_list += "--delete" }

    try {
        $output = & uv run python @args_list 2>&1
        $exitCode = $LASTEXITCODE

        if ($exitCode -ne 0) {
            Write-Host " [CRASH exit=$exitCode]" -ForegroundColor Red
            $fouten += $naam
            continue
        }

        # Parseer statistieken uit de output
        foreach ($line in $output) {
            if ($line -match "PDFs\s*:\s*([\d,]+)") {
                $totaalPDFs += [int]($Matches[1] -replace ",","")
            }
            if ($line -match "Bewaard \(relevant\)\s*:\s*([\d,]+)") {
                $totaalBewaard += [int]($Matches[1] -replace ",","")
            }
            if ($line -match "Bewaard \(gescand\)\s*:\s*([\d,]+)") {
                $totaalGescand += [int]($Matches[1] -replace ",","")
            }
            if ($line -match "Te verwijderen\s*:\s*([\d,]+)") {
                $totaalVerwijderd += [int]($Matches[1] -replace ",","")
            }
            if ($line -match "Vrij te maken\s*:\s*([\d,.]+)\s*MB") {
                $totaalMB += [double]($Matches[1] -replace ",",".")
            }
        }

        Write-Host " OK"
    } catch {
        Write-Host " [FOUT: $_]" -ForegroundColor Red
        $fouten += $naam
    }
}

$modus = if ($Delete) { "VERWIJDERD" } else { "DRY RUN" }
Write-Host ""
Write-Host "=== Totaaloverzicht [$modus] ==="
Write-Host ("  Gemeenten verwerkt : {0,6}" -f ($totaalGemeenten - $fouten.Count))
Write-Host ("  PDFs geanalyseerd  : {0,6:N0}" -f $totaalPDFs)
Write-Host ("  Bewaard (relevant) : {0,6:N0}" -f $totaalBewaard)
Write-Host ("  Bewaard (gescand)  : {0,6:N0}" -f $totaalGescand)
if ($Delete) {
    Write-Host ("  Verwijderd         : {0,6:N0}" -f $totaalVerwijderd)
    Write-Host ("  Vrijgemaakt        : {0,9:N1} MB" -f $totaalMB)
} else {
    Write-Host ("  Te verwijderen     : {0,6:N0}" -f $totaalVerwijderd)
    Write-Host ("  Vrij te maken      : {0,9:N1} MB" -f $totaalMB)
}

if ($fouten.Count -gt 0) {
    Write-Host ""
    Write-Host ("  Gemeenten met crash ({0}):" -f $fouten.Count) -ForegroundColor Yellow
    $fouten | ForEach-Object { Write-Host "    - $_" -ForegroundColor Yellow }
}
