function Resolve-WlocLanguage {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [System.IO.FileInfo]$File
    )

    $definitions = Get-WlocLanguageDefinitions
    $name = $File.Name
    $extension = $File.Extension.ToLowerInvariant()

    foreach ($definition in $definitions) {
        if ($definition.FileNames -contains $name) {
            return $definition
        }
    }

    foreach ($definition in $definitions) {
        if ($definition.Extensions -contains $extension) {
            return $definition
        }
    }

    [pscustomobject]@{
        Name = if ([string]::IsNullOrWhiteSpace($extension)) { 'Plain Text' } else { $extension.TrimStart('.').ToUpperInvariant() }
        Extensions = @($extension)
        FileNames = @($name)
        LineComments = @()
        BlockComments = @()
    }
}
