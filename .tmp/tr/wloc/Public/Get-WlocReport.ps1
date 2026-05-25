function Get-WlocReport {
    [CmdletBinding()]
    param(
        [string]$Path = '.',

        [string[]]$Include = @(),

        [string[]]$Exclude = @()
    )

    $resolvedPath = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    $fileMetrics = New-Object System.Collections.Generic.List[object]

    foreach ($file in Get-WlocCandidateFiles -Path $resolvedPath -Include $Include -Exclude $Exclude) {
        $metric = Get-WlocFileClassification -File $file -RootPath $resolvedPath
        if ($null -ne $metric) {
            $fileMetrics.Add($metric)
        }
    }

    $fileMetricArray = $fileMetrics.ToArray()

    $languageSummaryList = New-Object System.Collections.Generic.List[object]
    foreach ($group in ($fileMetricArray | Group-Object -Property Language | Sort-Object -Property Name)) {
        $languageSummaryList.Add([pscustomobject]@{
            Language = $group.Name
            Files = [int](($group.Group | Measure-Object -Property Files -Sum).Sum)
            Blank = [int](($group.Group | Measure-Object -Property Blank -Sum).Sum)
            Comment = [int](($group.Group | Measure-Object -Property Comment -Sum).Sum)
            Code = [int](($group.Group | Measure-Object -Property Code -Sum).Sum)
            Total = [int](($group.Group | Measure-Object -Property Total -Sum).Sum)
        })
    }

    $blankTotal = [int](($fileMetricArray | Measure-Object -Property Blank -Sum).Sum)
    $commentTotal = [int](($fileMetricArray | Measure-Object -Property Comment -Sum).Sum)
    $codeTotal = [int](($fileMetricArray | Measure-Object -Property Code -Sum).Sum)
    $lineTotal = [int](($fileMetricArray | Measure-Object -Property Total -Sum).Sum)

    [pscustomobject][ordered]@{
        Path = $resolvedPath
        ScannedAt = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz')
        Totals = [pscustomobject][ordered]@{
            Files = $fileMetricArray.Count
            Blank = $blankTotal
            Comment = $commentTotal
            Code = $codeTotal
            Total = $lineTotal
        }
        Languages = $languageSummaryList.ToArray()
        Files = $fileMetricArray
    }
}
