function Get-WlocLanguageDefinitions {
    [CmdletBinding()]
    param()

    @(
        [pscustomobject]@{
            Name = 'PowerShell'
            Extensions = @('.ps1', '.psm1', '.psd1', '.ps1xml')
            FileNames = @()
            LineComments = @('#')
            BlockComments = @(
                [pscustomobject]@{ Start = '<#'; End = '#>' }
            )
        }
        [pscustomobject]@{
            Name = 'JavaScript'
            Extensions = @('.js', '.jsx', '.mjs', '.cjs')
            FileNames = @()
            LineComments = @('//')
            BlockComments = @(
                [pscustomobject]@{ Start = '/*'; End = '*/' }
            )
        }
        [pscustomobject]@{
            Name = 'TypeScript'
            Extensions = @('.ts', '.tsx')
            FileNames = @()
            LineComments = @('//')
            BlockComments = @(
                [pscustomobject]@{ Start = '/*'; End = '*/' }
            )
        }
        [pscustomobject]@{
            Name = 'Python'
            Extensions = @('.py')
            FileNames = @()
            LineComments = @('#')
            BlockComments = @(
                [pscustomobject]@{ Start = "'''"; End = "'''" }
                [pscustomobject]@{ Start = '"""'; End = '"""' }
            )
        }
        [pscustomobject]@{
            Name = 'C#'
            Extensions = @('.cs')
            FileNames = @()
            LineComments = @('//')
            BlockComments = @(
                [pscustomobject]@{ Start = '/*'; End = '*/' }
            )
        }
        [pscustomobject]@{
            Name = 'Go'
            Extensions = @('.go')
            FileNames = @()
            LineComments = @('//')
            BlockComments = @(
                [pscustomobject]@{ Start = '/*'; End = '*/' }
            )
        }
        [pscustomobject]@{
            Name = 'Java'
            Extensions = @('.java')
            FileNames = @()
            LineComments = @('//')
            BlockComments = @(
                [pscustomobject]@{ Start = '/*'; End = '*/' }
            )
        }
        [pscustomobject]@{
            Name = 'C/C++'
            Extensions = @('.c', '.cc', '.cpp', '.h', '.hh', '.hpp')
            FileNames = @()
            LineComments = @('//')
            BlockComments = @(
                [pscustomobject]@{ Start = '/*'; End = '*/' }
            )
        }
        [pscustomobject]@{
            Name = 'Rust'
            Extensions = @('.rs')
            FileNames = @()
            LineComments = @('//')
            BlockComments = @(
                [pscustomobject]@{ Start = '/*'; End = '*/' }
            )
        }
        [pscustomobject]@{
            Name = 'HTML'
            Extensions = @('.html', '.htm')
            FileNames = @()
            LineComments = @()
            BlockComments = @(
                [pscustomobject]@{ Start = '<!--'; End = '-->' }
            )
        }
        [pscustomobject]@{
            Name = 'CSS'
            Extensions = @('.css', '.scss', '.sass', '.less')
            FileNames = @()
            LineComments = @()
            BlockComments = @(
                [pscustomobject]@{ Start = '/*'; End = '*/' }
            )
        }
        [pscustomobject]@{
            Name = 'SQL'
            Extensions = @('.sql')
            FileNames = @()
            LineComments = @('--')
            BlockComments = @(
                [pscustomobject]@{ Start = '/*'; End = '*/' }
            )
        }
        [pscustomobject]@{
            Name = 'Shell'
            Extensions = @('.sh', '.bash', '.zsh')
            FileNames = @()
            LineComments = @('#')
            BlockComments = @()
        }
        [pscustomobject]@{
            Name = 'JSON'
            Extensions = @('.json', '.jsonc')
            FileNames = @()
            LineComments = @('//')
            BlockComments = @(
                [pscustomobject]@{ Start = '/*'; End = '*/' }
            )
        }
        [pscustomobject]@{
            Name = 'YAML'
            Extensions = @('.yml', '.yaml')
            FileNames = @()
            LineComments = @('#')
            BlockComments = @()
        }
        [pscustomobject]@{
            Name = 'XML'
            Extensions = @('.xml', '.xaml', '.csproj', '.props', '.targets', '.config')
            FileNames = @()
            LineComments = @()
            BlockComments = @(
                [pscustomobject]@{ Start = '<!--'; End = '-->' }
            )
        }
        [pscustomobject]@{
            Name = 'Markdown'
            Extensions = @('.md', '.markdown')
            FileNames = @()
            LineComments = @()
            BlockComments = @()
        }
        [pscustomobject]@{
            Name = 'Docker'
            Extensions = @()
            FileNames = @('Dockerfile')
            LineComments = @('#')
            BlockComments = @()
        }
        [pscustomobject]@{
            Name = 'Vue'
            Extensions = @('.vue')
            FileNames = @()
            LineComments = @('//')
            BlockComments = @(
                [pscustomobject]@{ Start = '/*'; End = '*/' }
                [pscustomobject]@{ Start = '<!--'; End = '-->' }
            )
        }
        [pscustomobject]@{
            Name = 'Svelte'
            Extensions = @('.svelte')
            FileNames = @()
            LineComments = @('//')
            BlockComments = @(
                [pscustomobject]@{ Start = '/*'; End = '*/' }
                [pscustomobject]@{ Start = '<!--'; End = '-->' }
            )
        }
        [pscustomobject]@{
            Name = 'Plain Text'
            Extensions = @('.txt', '.gitignore', '.gitattributes', '.editorconfig')
            FileNames = @()
            LineComments = @()
            BlockComments = @()
        }
    )
}
