# Unified trees + municipal boundaries with Back Bay and Arboretum insets.
# Run: pwsh -File scripts/render_boston_followups.ps1
$ErrorActionPreference = 'Stop'
$renderer = Join-Path $PSScriptRoot 'render_boston_sources_map.py'
$frame = Join-Path $PSScriptRoot 'boston_sharing_frame.json'
$outputFolder = Join-Path $PSScriptRoot 'boston_followups'
$commonArgs = @('--palette', 'light', '--inset', 'back_bay_public_garden',
    '--inset-canopy', '--north-shift-km', '1.5', '--frame-from', $frame,
    '--compact', '--cartographic-details', '--unified', '--municipal-boundaries', '--second-inset', 'arboretum')
$variants = @(
    @{ Name = 'boston_treelines_back_bay_and_arboretum'; Extra = @() },
    @{ Name = 'boston_treelines_back_bay_and_arboretum_high_resolution'; Extra = @('--dpi', '600') }
)
$reference = $null
foreach ($variant in $variants) {
    $output = Join-Path $outputFolder ($variant.Name + '.png')
    $extraArgs = $variant.Extra
    uv run $renderer @commonArgs @extraArgs --output $output
    if ($LASTEXITCODE -ne 0) { throw "Rendering $($variant.Name) failed." }
    $metadata = Get-Content -LiteralPath ([IO.Path]::ChangeExtension($output, '.json')) -Raw | ConvertFrom-Json
    $signature = @($metadata.published_updated_through_range, $metadata.counts, $metadata.inset.counts_by_source, $metadata.second_inset.counts_by_source) | ConvertTo-Json -Depth 10 -Compress
    if ($null -ne $reference -and $signature -ne $reference) {
        throw 'Published data changed during rendering. Regenerate to keep the two versions consistent.'
    }
    $reference = $signature
}
