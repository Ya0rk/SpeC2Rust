function Format-WlocReport {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [pscustomobject]$Report,

        [ValidateSet('Code', 'Files', 'Name')]
        [string]$SortBy = 'Code',

        [ValidateRange(1, 100)]
        [int]$Top = 10,

        [switch]$NoChart
    )

    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("WLOC report for $($Report.Path)")
    $lines.Add("Scanned at: $($Report.ScannedAt)")
    $lines.Add('')

    $lines.Add('Totals')
    $lines.Add('')
    $totalsTable = @(
        [pscustomobject]@{
            Files = $Report.Totals.Files
            Blank = $Report.Totals.Blank
            Comment = $Report.Totals.Comment
            Code = $Report.Totals.Code
            Total = $Report.Totals.Total
        }
    ) | Format-Table -AutoSize | Out-String -Width 220
    $lines.AddRange(($totalsTable.TrimEnd() -split [Environment]::NewLine))
    $lines.Add('')

    $lines.Add('By language')
    $lines.Add('')

    $orderedLanguages = switch ($SortBy) {
        'Files' { $Report.Languages | Sort-Object -Property @{ Expression = 'Files'; Descending = $true }, @{ Expression = 'Code'; Descending = $true }, Language }
        'Name' { $Report.Languages | Sort-Object -Property Language }
        default { $Report.Languages | Sort-Object -Property @{ Expression = 'Code'; Descending = $true }, @{ Expression = 'Files'; Descending = $true }, Language }
    }

    if ($orderedLanguages.Count -eq 0) {
        $lines.Add('No analyzable files were found.')
        return $lines
    }

    $languageTable = $orderedLanguages |
        Select-Object Language, Files, Blank, Comment, Code, Total |
        Format-Table -AutoSize |
        Out-String -Width 220
    $lines.AddRange(($languageTable.TrimEnd() -split [Environment]::NewLine))

    if ($NoChart) {
        return $lines
    }

    $lines.Add('')
    $lines.Add('Code volume')
    $lines.Add('')

    $chartItems = $orderedLanguages | Select-Object -First $Top
    $maxCode = ($chartItems | Measure-Object -Property Code -Maximum).Maximum
    $labelWidth = 1
    foreach ($chartItem in $chartItems) {
        if ($chartItem.Language.Length -gt $labelWidth) {
            $labelWidth = $chartItem.Language.Length
        }
    }

    foreach ($item in $chartItems) {
        $barWidth = if ($maxCode -le 0) { 0 } else { [Math]::Max([int][Math]::Round(($item.Code / $maxCode) * 40), 1) }
        $bar = if ($item.Code -le 0) { '' } else { ''.PadLeft($barWidth, [char]0x2588) }
        $lines.Add(('{0} | {1} {2}' -f $item.Language.PadRight($labelWidth), $bar, $item.Code))
    }

    $lines
}
