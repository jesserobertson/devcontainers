#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Build or pull all devcontainer images and features.

.DESCRIPTION
    By default builds Docker images locally and packages features using the devcontainer CLI.
    Use -Pull to pull everything from GHCR instead.

.PARAMETER Pull
    Pull images and features from GHCR (ghcr.io/jesserobertson) instead of building locally.

.PARAMETER Images
    Images to process. Defaults to all: base-ubuntu, base-ubuntu-slim, base-cuda, base-cuda-slim, ramalama.
    Note: ramalama depends on base-cuda, so base-cuda is always included when ramalama is selected.
    base-ubuntu / base-cuda are assembled from their -slim base via `devcontainer build`, so the
    matching -slim image is auto-added when a bundle is selected without it.

.PARAMETER Features
    Features to process. Defaults to all features in the features/ directory.
    Pass an empty array to skip features: -Features @()

.PARAMETER SkipImages
    Skip Docker image processing entirely.

.PARAMETER SkipFeatures
    Skip devcontainer feature processing entirely.

.PARAMETER OutputDir
    Directory to write packaged feature tarballs when building locally. Defaults to .features-pkg/

.EXAMPLE
    .\build-images.ps1
    # Build all images locally and package all features

.EXAMPLE
    .\build-images.ps1 -Pull
    # Pull all images and features from GHCR

.EXAMPLE
    .\build-images.ps1 -SkipFeatures
    # Build only the Docker images

.EXAMPLE
    .\build-images.ps1 -Pull -SkipImages -Features ramalama,cli
    # Pull only specific features from GHCR
#>
param(
    [switch]$Pull,
    [switch]$SkipImages,
    [switch]$SkipFeatures,

    [ValidateSet('base-ubuntu', 'base-ubuntu-slim', 'base-cuda', 'base-cuda-slim', 'ramalama')]
    [string[]]$Images = @('base-ubuntu', 'base-ubuntu-slim', 'base-cuda', 'base-cuda-slim', 'ramalama'),

    [ValidateSet('cli', 'fastapi', 'homebrew', 'huggingface', 'jax', 'marimo', 'mojo',
                 'pixi', 'podman', 'py-devtools', 'pytorch', 'ramalama', 'rapids',
                 'shell-kit', 'transformers')]
    [string[]]$Features = @('cli', 'fastapi', 'homebrew', 'huggingface', 'jax', 'marimo', 'mojo',
                             'pixi', 'podman', 'py-devtools', 'pytorch', 'ramalama', 'rapids',
                             'shell-kit', 'transformers'),

    [string]$OutputDir = '.features-pkg'
)

function Invoke-DevcontainerBuild {
    [CmdletBinding()]
    param([string]$ConfigDir, [string[]]$Tags, [switch]$Push)

    if (-not (Get-Command devcontainer -ErrorAction SilentlyContinue)) {
        throw "devcontainer CLI not found. Install it with: npm install -g @devcontainers/cli"
    }
    $args = @('build', '--workspace-folder', $ConfigDir)
    foreach ($tag in $Tags) { $args += '--image-name', $tag }
    if ($Push) { $args += '--push', 'true' }
    Write-Host "    devcontainer $($args -join ' ')"
    devcontainer @args
    if ($LASTEXITCODE -ne 0) { throw "devcontainer build failed for $ConfigDir" }
}

function Invoke-Build {
    [CmdletBinding()]
    param(
        [switch]$Pull,
        [switch]$SkipImages,
        [switch]$SkipFeatures,
        [string[]]$Images,
        [string[]]$Features,
        [string]$OutputDir
    )

    $ErrorActionPreference = 'Stop'

    $Registry = 'ghcr.io'
    $Owner    = 'jesserobertson'

    $ImageDefs = [ordered]@{
        'base-ubuntu' = @{
            Builder   = 'devcontainer'
            ConfigDir = 'images/base-ubuntu'
            Tags      = @("$Registry/$Owner/base-ubuntu:latest")
        }
        'base-ubuntu-slim' = @{
            Context   = 'base'
            Target    = 'slim'
            BuildArgs = @{ BASE_IMAGE = 'ubuntu:24.04' }
            Tags      = @("$Registry/$Owner/base-ubuntu-slim:latest")
        }
        'base-cuda' = @{
            Builder   = 'devcontainer'
            ConfigDir = 'images/base-cuda'
            Tags      = @(
                "$Registry/$Owner/base-cuda:latest",
                "$Registry/$Owner/base-cuda:cuda12.8.0"
            )
        }
        'base-cuda-slim' = @{
            Context   = 'base'
            Target    = 'slim'
            BuildArgs = @{ BASE_IMAGE = 'nvidia/cuda:12.8.0-devel-ubuntu24.04' }
            Tags      = @("$Registry/$Owner/base-cuda-slim:latest")
        }
        'ramalama' = @{
            Context   = 'ramalama'
            BuildArgs = @{}
            Tags      = @("$Registry/$Owner/ramalama:latest")
        }
    }

    # ---------------------------------------------------------------------------
    # Docker images
    # ---------------------------------------------------------------------------

    if (-not $SkipImages) {
        if ('ramalama' -in $Images -and 'base-cuda' -notin $Images) {
            Write-Warning "ramalama depends on base-cuda — adding base-cuda to the image list."
            $Images = @('base-cuda') + $Images
        }

        $bundleToSlim = @{ 'base-ubuntu' = 'base-ubuntu-slim'; 'base-cuda' = 'base-cuda-slim' }
        foreach ($bundle in $bundleToSlim.Keys) {
            $slim = $bundleToSlim[$bundle]
            if ($bundle -in $Images -and $slim -notin $Images) {
                Write-Warning "$bundle is assembled from $slim — adding $slim to the image list."
                $Images = @($slim) + $Images
            }
        }

        $selectedImages = $ImageDefs.Keys | Where-Object { $_ -in $Images }

        Write-Host "`n== Docker images ==" -ForegroundColor Yellow

        foreach ($name in $selectedImages) {
            $def = $ImageDefs[$name]
            $primaryTag = $def.Tags[0]

            if ($Pull) {
                Write-Host "`n--> Pulling $name ($primaryTag)" -ForegroundColor Cyan
                docker pull $primaryTag
                if ($LASTEXITCODE -ne 0) { throw "docker pull failed for $primaryTag" }

                foreach ($tag in $def.Tags | Select-Object -Skip 1) {
                    docker tag $primaryTag $tag
                    if ($LASTEXITCODE -ne 0) { throw "docker tag failed: $primaryTag -> $tag" }
                    Write-Host "    Tagged $tag"
                }
            } else {
                Write-Host "`n--> Building $name" -ForegroundColor Cyan

                if ($def.Builder -eq 'devcontainer') {
                    Invoke-DevcontainerBuild -ConfigDir $def.ConfigDir -Tags $def.Tags
                } else {
                    $buildArgs = @('build')
                    foreach ($tag in $def.Tags)                       { $buildArgs += '--tag', $tag }
                    foreach ($kv in $def.BuildArgs.GetEnumerator())   { $buildArgs += '--build-arg', "$($kv.Key)=$($kv.Value)" }
                    if ($def.Target)                                  { $buildArgs += '--target', $def.Target }
                    $buildArgs += $def.Context

                    Write-Host "    docker $($buildArgs -join ' ')"
                    docker @buildArgs
                    if ($LASTEXITCODE -ne 0) { throw "docker build failed for $name" }
                }
            }

            Write-Host "    Done: $($def.Tags -join ', ')" -ForegroundColor Green
        }
    }

    # ---------------------------------------------------------------------------
    # Devcontainer features
    # ---------------------------------------------------------------------------

    if (-not $SkipFeatures -and $Features.Count -gt 0) {
        Write-Host "`n== Devcontainer features ==" -ForegroundColor Yellow

        if ($Pull) {
            foreach ($id in $Features) {
                $ref = "$Registry/$Owner/devcontainer-feature-$id`:latest"
                Write-Host "`n--> Pulling feature $id ($ref)" -ForegroundColor Cyan
                docker pull $ref
                if ($LASTEXITCODE -ne 0) { throw "docker pull failed for feature $id" }
                Write-Host "    Done" -ForegroundColor Green
            }
        } else {
            if (-not (Get-Command devcontainer -ErrorAction SilentlyContinue)) {
                throw "devcontainer CLI not found. Install it with: npm install -g @devcontainers/cli"
            }

            New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
            $absOutput = (Resolve-Path $OutputDir).Path

            foreach ($id in $Features) {
                $featureDir = "features/$id"
                if (-not (Test-Path $featureDir)) {
                    Write-Warning "Feature directory '$featureDir' not found — skipping."
                    continue
                }

                Write-Host "`n--> Packaging feature $id -> $absOutput" -ForegroundColor Cyan
                devcontainer features package $featureDir --output-folder $absOutput --force-clean-output-folder
                if ($LASTEXITCODE -ne 0) { throw "devcontainer features package failed for $id" }
                Write-Host "    Done" -ForegroundColor Green
            }

            Write-Host "`n    Packaged features written to: $absOutput" -ForegroundColor DarkGray
        }
    }

    Write-Host "`nAll done." -ForegroundColor Green
}

# Run when executed directly; skip when dot-sourced (e.g. by tests)
if ($MyInvocation.InvocationName -ne '.') {
    Invoke-Build -Pull:$Pull -SkipImages:$SkipImages -SkipFeatures:$SkipFeatures `
                 -Images $Images -Features $Features -OutputDir $OutputDir
}
