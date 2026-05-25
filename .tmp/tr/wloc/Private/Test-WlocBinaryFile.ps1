function Test-WlocBinaryFile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    $binaryExtensions = @(
        '.7z', '.a', '.bmp', '.class', '.dll', '.dylib', '.exe', '.gif', '.gz', '.ico',
        '.jar', '.jpeg', '.jpg', '.lib', '.mp3', '.mp4', '.nupkg', '.pdf', '.png',
        '.pdb', '.so', '.svgz', '.tar', '.tif', '.tiff', '.ttf', '.wav', '.webm',
        '.woff', '.woff2', '.zip'
    )

    $extension = [System.IO.Path]::GetExtension($Path).ToLowerInvariant()
    if ($binaryExtensions -contains $extension) {
        return $true
    }

    $stream = $null
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        $buffer = New-Object byte[] 4096
        $bytesRead = $stream.Read($buffer, 0, $buffer.Length)

        if ($bytesRead -ge 4) {
            $hasTextBom = (
                ($buffer[0] -eq 0xEF -and $buffer[1] -eq 0xBB -and $buffer[2] -eq 0xBF) -or
                ($buffer[0] -eq 0xFF -and $buffer[1] -eq 0xFE) -or
                ($buffer[0] -eq 0xFE -and $buffer[1] -eq 0xFF) -or
                ($buffer[0] -eq 0xFF -and $buffer[1] -eq 0xFE -and $buffer[2] -eq 0x00 -and $buffer[3] -eq 0x00) -or
                ($buffer[0] -eq 0x00 -and $buffer[1] -eq 0x00 -and $buffer[2] -eq 0xFE -and $buffer[3] -eq 0xFF)
            )

            if ($hasTextBom) {
                return $false
            }
        }

        $controlBytes = 0
        for ($index = 0; $index -lt $bytesRead; $index++) {
            $byte = $buffer[$index]

            if ($byte -eq 0x00) {
                return $true
            }

            if ($byte -lt 0x09 -or ($byte -gt 0x0D -and $byte -lt 0x20)) {
                $controlBytes++
            }
        }

        if ($bytesRead -gt 0 -and ($controlBytes / $bytesRead) -gt 0.30) {
            return $true
        }

        return $false
    }
    finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
}
