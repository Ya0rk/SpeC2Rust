@{
    RootModule = 'wloc.psm1'
    ModuleVersion = '0.1.0'
    GUID = 'fce0f31c-f5a0-4dc0-8a77-95fe84078a8b'
    Author = 'wloc contributors'
    CompanyName = 'Community'
    Copyright = '(c) 2026'
    Description = 'A PowerShell CLI that counts files, blank lines, comment lines, and code lines in project directories.'
    PowerShellVersion = '7.6.1'
    FunctionsToExport = @(
        'Get-WlocReport',
        'Invoke-Wloc'
    )
    AliasesToExport = @(
        'wloc'
    )
    CmdletsToExport = @()
    VariablesToExport = @()
    PrivateData = @{
        PSData = @{
            Tags = @('windows', 'powershell', 'cli', 'loc', 'code-stats')
            LicenseUri = 'https://opensource.org/licenses/MIT'
            ProjectUri = 'https://github.com/<your-account>/wloc'
            ReleaseNotes = 'Initial release.'
        }
    }
}
