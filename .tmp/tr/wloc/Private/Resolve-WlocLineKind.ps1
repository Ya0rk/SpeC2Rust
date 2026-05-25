function Resolve-WlocLineKind {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string]$Line,

        [string[]]$LineCommentTokens = @(),

        [object[]]$BlockCommentTokens = @(),

        [ref]$BlockState
    )

    $position = 0
    $lineLength = $Line.Length
    $sawCode = $false
    $sawComment = $false
    $comparison = [System.StringComparison]::OrdinalIgnoreCase

    while ($position -lt $lineLength) {
        if ($null -ne $BlockState.Value) {
            $sawComment = $true
            $endToken = $BlockState.Value.End
            $endIndex = $Line.IndexOf($endToken, $position, $comparison)

            if ($endIndex -lt 0) {
                $position = $lineLength
                break
            }

            $position = $endIndex + $endToken.Length
            $BlockState.Value = $null
            continue
        }

        $remaining = $Line.Substring($position)
        if ([string]::IsNullOrWhiteSpace($remaining)) {
            break
        }

        $nextToken = $null
        $nearestIndex = [int]::MaxValue

        foreach ($token in $LineCommentTokens) {
            $tokenIndex = $remaining.IndexOf($token, $comparison)
            if ($tokenIndex -ge 0 -and $tokenIndex -lt $nearestIndex) {
                $nearestIndex = $tokenIndex
                $nextToken = [pscustomobject]@{
                    Type = 'Line'
                    Token = $token
                    Index = $tokenIndex
                }
            }
        }

        foreach ($token in $BlockCommentTokens) {
            $tokenIndex = $remaining.IndexOf($token.Start, $comparison)
            if ($tokenIndex -ge 0 -and $tokenIndex -lt $nearestIndex) {
                $nearestIndex = $tokenIndex
                $nextToken = [pscustomobject]@{
                    Type = 'Block'
                    Token = $token
                    Index = $tokenIndex
                }
            }
        }

        if ($null -eq $nextToken) {
            if (-not [string]::IsNullOrWhiteSpace($remaining)) {
                $sawCode = $true
            }

            break
        }

        if ($nextToken.Index -gt 0) {
            $prefix = $remaining.Substring(0, $nextToken.Index)
            if (-not [string]::IsNullOrWhiteSpace($prefix)) {
                $sawCode = $true
            }

            $position += $nextToken.Index
        }

        if ($nextToken.Type -eq 'Line') {
            $sawComment = $true
            $position = $lineLength
            break
        }

        $sawComment = $true
        $position += $nextToken.Token.Start.Length
        $BlockState.Value = $nextToken.Token
    }

    if ($sawCode) {
        return 'Code'
    }

    if ($sawComment) {
        return 'Comment'
    }

    if ([string]::IsNullOrWhiteSpace($Line)) {
        return 'Blank'
    }

    'Code'
}
