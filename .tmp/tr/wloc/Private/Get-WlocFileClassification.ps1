function Get-WlocFileClassification {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [System.IO.FileInfo]$File,

        [Parameter(Mandatory)]
        [string]$RootPath
    )

    $language = Resolve-WlocLanguage -File $File

    if (Test-WlocBinaryFile -Path $File.FullName) {
        return $null
    }

    $lines = Get-Content -LiteralPath $File.FullName -ErrorAction Stop
    $blockState = $null

    $blank = 0
    $comment = 0
    $code = 0

    foreach ($line in $lines) {
        $lineKind = Resolve-WlocLineKind `
            -Line $line `
            -LineCommentTokens $language.LineComments `
            -BlockCommentTokens $language.BlockComments `
            -BlockState ([ref]$blockState)

        switch ($lineKind) {
            'Blank' { $blank++ }
            'Comment' { $comment++ }
            default { $code++ }
        }
    }

    $relativePath = [System.IO.Path]::GetRelativePath($RootPath, $File.FullName)
    $total = $blank + $comment + $code

    [pscustomobject]@{
        Path = $File.FullName
        RelativePath = $relativePath
        Language = $language.Name
        Files = 1
        Blank = $blank
        Comment = $comment
        Code = $code
        Total = $total
    }
}
