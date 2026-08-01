param(
  [string]$CsvFile = "issues.csv",
  [string]$Repo = "knowledge-corner/cemh"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
  throw "GitHub CLI not found. Install: winget install --id GitHub.cli -e"
}

if (-not (Test-Path $CsvFile)) {
  throw "CSV file not found: $CsvFile"
}

gh auth status | Out-Null
$rows = Import-Csv -Path $CsvFile

foreach ($r in $rows) {
  if ([string]::IsNullOrWhiteSpace($r.Ref)) { continue }

  $title = "$($r.Ref) - $($r.Element)"

  $body = @"
## Reference
$($r.Ref)

## Screen
$($r.Screen)

## Element
$($r.Element)

## Type
$($r.Type)

## Description
$($r.Description)

## Rules and behaviour
$($r.'Rules and behaviour')

## Applies to
$($r.'Applies to')
"@

  Write-Host "Creating issue: $title"

  gh issue create `
    --repo $Repo `
    --title $title `
    --body $body
}

Write-Host ""
Write-Host "Done. Check: https://github.com/$Repo/issues"