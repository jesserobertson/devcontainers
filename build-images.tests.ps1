#!/usr/bin/env pwsh
#Requires -Modules Pester

BeforeAll {
    # Dot-source to load Invoke-Build without executing the script body
    . "$PSScriptRoot/build-images.ps1"

    # Stub functions so Pester can Mock them — Pester can only mock commands that
    # already exist as functions/cmdlets; external programs on PATH need a shim.
    function docker { }
    function devcontainer { }

    # Helper: call Invoke-Build with safe defaults, overriding only what each test needs
    function Invoke-BuildDefault ([hashtable]$Params = @{}) {
        $defaults = @{
            Images       = @('base-ubuntu')
            Features     = @('cli')
            OutputDir    = 'TestDrive:\pkg'
            SkipImages   = $false
            SkipFeatures = $false
            Pull         = $false
        }
        foreach ($key in $Params.Keys) { $defaults[$key] = $Params[$key] }
        Invoke-Build @defaults
    }
}

Describe 'Invoke-Build — image build mode' {
    BeforeEach {
        Mock docker { $global:LASTEXITCODE = 0 }
    }

    It 'calls docker build with correct context for base-ubuntu' {
        Invoke-BuildDefault @{ SkipFeatures = $true; Images = @('base-ubuntu') }

        Should -Invoke docker -Times 1 -ParameterFilter {
            $args -contains 'build' -and
            $args -contains 'base' -and
            $args -contains 'ghcr.io/jesserobertson/base-ubuntu:latest'
        }
    }

    It 'passes BASE_IMAGE build-arg for base-ubuntu' {
        Invoke-BuildDefault @{ SkipFeatures = $true; Images = @('base-ubuntu') }

        Should -Invoke docker -ParameterFilter {
            ($args -join ' ') -match 'BASE_IMAGE=ubuntu:24\.04'
        }
    }

    It 'passes BASE_IMAGE build-arg for base-cuda' {
        Invoke-BuildDefault @{ SkipFeatures = $true; Images = @('base-cuda') }

        Should -Invoke docker -ParameterFilter {
            ($args -join ' ') -match 'BASE_IMAGE=nvidia/cuda'
        }
    }

    It 'tags base-cuda with both latest and versioned tag' {
        Invoke-BuildDefault @{ SkipFeatures = $true; Images = @('base-cuda') }

        Should -Invoke docker -ParameterFilter {
            $args -contains 'ghcr.io/jesserobertson/base-cuda:latest' -and
            $args -contains 'ghcr.io/jesserobertson/base-cuda:cuda12.8.0'
        }
    }

    It 'uses ramalama context for the ramalama image' {
        Invoke-BuildDefault @{ SkipFeatures = $true; Images = @('base-cuda', 'ramalama') }

        Should -Invoke docker -ParameterFilter {
            $args -contains 'build' -and
            $args -contains 'ramalama' -and
            $args -contains 'ghcr.io/jesserobertson/ramalama:latest'
        }
    }

    It 'throws when docker build returns non-zero' {
        Mock docker { $global:LASTEXITCODE = 1 }

        { Invoke-BuildDefault @{ SkipFeatures = $true; Images = @('base-ubuntu') } } |
            Should -Throw -ExpectedMessage '*docker build failed*'
    }
}

Describe 'Invoke-Build — image pull mode' {
    BeforeEach {
        Mock docker { $global:LASTEXITCODE = 0 }
    }

    It 'calls docker pull with the primary tag' {
        Invoke-BuildDefault @{ Pull = $true; SkipFeatures = $true; Images = @('base-ubuntu') }

        Should -Invoke docker -ParameterFilter {
            $args -contains 'pull' -and
            $args -contains 'ghcr.io/jesserobertson/base-ubuntu:latest'
        }
    }

    It 'tags the versioned alias after pulling base-cuda' {
        Invoke-BuildDefault @{ Pull = $true; SkipFeatures = $true; Images = @('base-cuda') }

        Should -Invoke docker -ParameterFilter {
            $args -contains 'tag' -and
            $args -contains 'ghcr.io/jesserobertson/base-cuda:cuda12.8.0'
        }
    }

    It 'throws when docker pull returns non-zero' {
        Mock docker { $global:LASTEXITCODE = 1 }

        { Invoke-BuildDefault @{ Pull = $true; SkipFeatures = $true; Images = @('base-ubuntu') } } |
            Should -Throw -ExpectedMessage '*docker pull failed*'
    }
}

Describe 'Invoke-Build — ramalama dependency guard' {
    BeforeEach {
        Mock docker { $global:LASTEXITCODE = 0 }
    }

    It 'auto-adds base-cuda when only ramalama is requested' {
        Invoke-BuildDefault @{ SkipFeatures = $true; Images = @('ramalama') }

        Should -Invoke docker -ParameterFilter {
            ($args -join ' ') -match 'BASE_IMAGE=nvidia/cuda'
        }
    }

    It 'emits a warning when base-cuda is auto-added' {
        $warnings = Invoke-BuildDefault @{ SkipFeatures = $true; Images = @('ramalama') } 3>&1 |
            Where-Object { $_ -is [System.Management.Automation.WarningRecord] }

        $warnings.Message | Should -Match 'base-cuda'
    }
}

Describe 'Invoke-Build — feature pull mode' {
    BeforeEach {
        Mock docker { $global:LASTEXITCODE = 0 }
    }

    It 'pulls the correct OCI ref for a feature' {
        Invoke-BuildDefault @{ Pull = $true; SkipImages = $true; Features = @('cli') }

        Should -Invoke docker -ParameterFilter {
            $args -contains 'pull' -and
            $args -contains 'ghcr.io/jesserobertson/devcontainer-feature-cli:latest'
        }
    }

    It 'pulls each selected feature separately' {
        Invoke-BuildDefault @{ Pull = $true; SkipImages = $true; Features = @('cli', 'pytorch') }

        Should -Invoke docker -Times 1 -ParameterFilter {
            ($args -join ' ') -match 'devcontainer-feature-cli'
        }
        Should -Invoke docker -Times 1 -ParameterFilter {
            ($args -join ' ') -match 'devcontainer-feature-pytorch'
        }
    }

    It 'throws when docker pull fails for a feature' {
        Mock docker { $global:LASTEXITCODE = 1 }

        { Invoke-BuildDefault @{ Pull = $true; SkipImages = $true; Features = @('cli') } } |
            Should -Throw -ExpectedMessage '*docker pull failed for feature cli*'
    }
}

Describe 'Invoke-Build — feature package mode' {
    BeforeEach {
        Mock New-Item { }
        Mock Resolve-Path { [pscustomobject]@{ Path = 'TestDrive:\pkg' } }
        Mock Test-Path { $true }
        Mock devcontainer { $global:LASTEXITCODE = 0 }
    }

    It 'calls devcontainer features package with the feature dir and output folder' {
        Invoke-BuildDefault @{ SkipImages = $true; Features = @('cli') }

        Should -Invoke devcontainer -ParameterFilter {
            $args -contains 'features' -and
            $args -contains 'package' -and
            ($args -join ' ') -match 'features/cli'
        }
    }

    It 'packages each selected feature' {
        Invoke-BuildDefault @{ SkipImages = $true; Features = @('cli', 'pytorch') }

        Should -Invoke devcontainer -Times 1 -ParameterFilter {
            ($args -join ' ') -match 'features/cli'
        }
        Should -Invoke devcontainer -Times 1 -ParameterFilter {
            ($args -join ' ') -match 'features/pytorch'
        }
    }

    It 'throws when devcontainer CLI is not installed' {
        # Hide the stub so Get-Command returns nothing
        Mock Get-Command { $null } -ParameterFilter { $Name -eq 'devcontainer' }

        { Invoke-BuildDefault @{ SkipImages = $true; Features = @('cli') } } |
            Should -Throw -ExpectedMessage '*devcontainer CLI not found*'
    }

    It 'skips a missing feature directory and does not throw' {
        Mock Test-Path { $false }

        { Invoke-BuildDefault @{ SkipImages = $true; Features = @('cli') } } | Should -Not -Throw
        Should -Invoke devcontainer -Times 0
    }

    It 'throws when devcontainer package returns non-zero' {
        Mock devcontainer { $global:LASTEXITCODE = 1 }

        { Invoke-BuildDefault @{ SkipImages = $true; Features = @('cli') } } |
            Should -Throw -ExpectedMessage '*devcontainer features package failed for cli*'
    }
}

Describe 'Invoke-Build — skip flags' {
    BeforeEach {
        Mock docker { $global:LASTEXITCODE = 0 }
        Mock devcontainer { $global:LASTEXITCODE = 0 }
        Mock New-Item { }
        Mock Resolve-Path { [pscustomobject]@{ Path = 'TestDrive:\pkg' } }
        Mock Test-Path { $true }
    }

    It 'does not call docker when -SkipImages is set' {
        Invoke-BuildDefault @{ SkipImages = $true; Features = @('cli') }
        Should -Invoke docker -Times 0
    }

    It 'does not call devcontainer when -SkipFeatures is set' {
        Invoke-BuildDefault @{ SkipFeatures = $true; Images = @('base-ubuntu') }
        Should -Invoke devcontainer -Times 0
    }

    It 'does nothing when both skip flags are set' {
        Invoke-BuildDefault @{ SkipImages = $true; SkipFeatures = $true }
        Should -Invoke docker -Times 0
        Should -Invoke devcontainer -Times 0
    }
}
