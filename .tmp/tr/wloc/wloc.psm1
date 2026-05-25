$publicPath = Join-Path -Path $PSScriptRoot -ChildPath 'Public'
$privatePath = Join-Path -Path $PSScriptRoot -ChildPath 'Private'

Get-ChildItem -Path $privatePath -Filter '*.ps1' |
    Sort-Object -Property Name |
    ForEach-Object {
        . $_.FullName
    }

Get-ChildItem -Path $publicPath -Filter '*.ps1' |
    Sort-Object -Property Name |
    ForEach-Object {
        . $_.FullName
    }

Set-Alias -Name wloc -Value Invoke-Wloc

Export-ModuleMember -Function Get-WlocReport, Invoke-Wloc -Alias wloc
