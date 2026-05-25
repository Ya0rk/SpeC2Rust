function Get-WlocCandidateFiles {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [string[]]$Include = @(),

        [string[]]$Exclude = @()
    )

    $defaultExcludedDirectories = @(
        '.git', '.hg', '.svn', '.next', '.nuxt', '.venv', '.idea', '.vscode',
        'bin', 'build', 'coverage', 'dist', 'node_modules', 'obj', 'out',
        'packages', 'vendor', 'venv'
    )

    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $files = Get-ChildItem -LiteralPath $resolved -File -Recurse -Force

    foreach ($file in ($files | Sort-Object -Property FullName)) {
        $relativePath = [System.IO.Path]::GetRelativePath($resolved, $file.FullName)
        $segments = $relativePath -split '[\\/]'

        $skip = $false
        foreach ($segment in $segments) {
            if ($defaultExcludedDirectories -contains $segment) {
                $skip = $true
                break
            }
        }

        if ($skip) {
            continue
        }

        foreach ($pattern in $Exclude) {
            if ($relativePath -like $pattern -or $file.Name -like $pattern) {
                $skip = $true
                break
            }
        }

        if ($skip) {
            continue
        }

        if ($Include.Count -gt 0) {
            $matched = $false
            foreach ($pattern in $Include) {
                if ($relativePath -like $pattern -or $file.Name -like $pattern) {
                    $matched = $true
                    break
                }
            }

            if (-not $matched) {
                continue
            }
        }

        $file
    }
}
