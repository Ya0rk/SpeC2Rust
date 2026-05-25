<#
.SYNOPSIS
Scans a project and reports file count, blank lines, comment lines, code lines, and a code-volume chart.

.DESCRIPTION
Invoke-Wloc is the main CLI entrypoint for wloc. It recursively scans a directory,
classifies files by language, counts blank/comment/code lines, and renders a terminal summary.

.PARAMETER Path
The project directory to scan. Defaults to the current directory.

.PARAMETER Include
Optional wildcard patterns used to include only matching files.

.PARAMETER Exclude
Optional wildcard patterns used to skip matching files or paths.

.PARAMETER SortBy
Sorts language output by Code, Files, or Name.

.PARAMETER Top
Limits the chart to the top N languages.

.PARAMETER NoChart
Skips the code-volume chart output.

.PARAMETER Json
Outputs the report as JSON.

.PARAMETER PassThru
Returns the raw report object after printing the report.

.PARAMETER Help
Shows command help text.

.EXAMPLE
wloc .

.EXAMPLE
wloc . -Top 5

.EXAMPLE
wloc . -Json

.EXAMPLE
wloc -h
#>
function Invoke-Wloc {
    [CmdletBinding()]
    param(
        [Alias('h')]
        [switch]$Help,

        [string]$Path = '.',

        [string[]]$Include = @(),

        [string[]]$Exclude = @(),

        [ValidateSet('Code', 'Files', 'Name')]
        [string]$SortBy = 'Code',

        [ValidateRange(1, 100)]
        [int]$Top = 10,

        [switch]$NoChart,

        [switch]$Json,

        [switch]$PassThru
    )

    if ($Help) {
        Get-Help Invoke-Wloc -Detailed | Out-Host
        return
    }

    $report = Get-WlocReport -Path $Path -Include $Include -Exclude $Exclude

    if ($Json) {
        $report | ConvertTo-Json -Depth 6
    }
    else {
        Format-WlocReport -Report $report -SortBy $SortBy -Top $Top -NoChart:$NoChart |
            ForEach-Object {
                Write-Host $_
            }
    }

    if ($PassThru) {
        $report
    }
}
