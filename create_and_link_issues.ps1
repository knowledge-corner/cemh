param(
  [string]$CsvFile = "issues.csv",
  [string]$Repo = "knowledge-corner/cemh"
)

$ErrorActionPreference = "Stop"

function Require-Cmd($name) {
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
    throw "Required command not found: $name"
  }
}
Require-Cmd gh

if (-not (Test-Path $CsvFile)) {
  throw "CSV file not found: $CsvFile"
}

gh auth status | Out-Null

# Fetch all existing issues once
function Get-Issues {
  gh issue list --repo $Repo --state all --limit 1000 --json number,title,url | ConvertFrom-Json
}

Write-Host "Loading existing issues from $Repo ..."
$existing = Get-Issues
$titleMap = @{}
foreach ($i in $existing) { $titleMap[$i.title] = $i }

$rows = Import-Csv -Path $CsvFile

$parentGroups = @{}
$children = @()

foreach ($r in $rows) {
  if ([string]::IsNullOrWhiteSpace($r.Ref)) { continue }

  $ref = $r.Ref.Trim()
  $screen = $r.Screen.Trim()
  $element = $r.Element.Trim()

  if ($ref -match '^([A-Z]+-\d+)\.(\d+)$') {
    $parentRef = $matches[1]
    if (-not $parentGroups.ContainsKey($parentRef)) { $parentGroups[$parentRef] = $screen }

    $children += [PSCustomObject]@{
      Ref = $ref
      ParentRef = $parentRef
      Screen = $screen
      Element = $element
      Type = $r.Type
      Description = $r.Description
      Rules = $r.'Rules and behaviour'
      AppliesTo = $r.'Applies to'
    }
  } else {
    if (-not $parentGroups.ContainsKey($ref)) { $parentGroups[$ref] = $screen }
  }
}

function Ensure-Issue([string]$title, [string]$body) {
  if ($titleMap.ContainsKey($title)) { return $titleMap[$title] }

  Write-Host "Creating issue: $title"
  # Old-gh compatible: plain create output
  $createOutput = gh issue create --repo $Repo --title $title --body $body 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Failed creating issue: $title`n$createOutput"
  }

  # Refresh issue list and resolve by exact title
  $updated = Get-Issues
  $match = $updated | Where-Object { $_.title -eq $title } | Select-Object -First 1
  if (-not $match) {
    throw "Issue created but could not be found by title: $title"
  }

  $titleMap[$title] = $match
  return $match
}

$parentIssueByRef = @{}

# Create parents
foreach ($parentRef in $parentGroups.Keys) {
  $screen = $parentGroups[$parentRef]
  $parentTitle = "$parentRef - $screen"
  $parentBody = @"
## Reference
$parentRef

## Screen / Module
$screen

## Type
Parent requirement group

## Description
Tracks all child requirements under $parentRef.

## Notes
This issue is the parent for linked sub-issues.
"@
  $parentIssueByRef[$parentRef] = Ensure-Issue -title $parentTitle -body $parentBody
}

# Create children + attempt sub-issue link
foreach ($c in $children) {
  $childTitle = "$($c.Ref) - $($c.Element)"
  $childBody = @"
## Reference
$($c.Ref)

## Screen
$($c.Screen)

## Element
$($c.Element)

## Type
$($c.Type)

## Description
$($c.Description)

## Rules and behaviour
$($c.Rules)

## Applies to
$($c.AppliesTo)
"@

  $childIssue = Ensure-Issue -title $childTitle -body $childBody
  $parent = $parentIssueByRef[$c.ParentRef]

  if ($null -ne $parent) {
    Write-Host "Linking child #$($childIssue.number) -> parent #$($parent.number)"
    $linkOut = gh issue edit $childIssue.number --repo $Repo --add-parent $parent.number 2>&1
    if ($LASTEXITCODE -ne 0) {
      Write-Host "  (Could not link automatically; continuing)"
    }
  }
}

Write-Host ""
Write-Host "Done."
Write-Host "Issues: https://github.com/$Repo/issues"
Write-Host "Project: https://github.com/users/knowledge-corner/projects/1/views/9"