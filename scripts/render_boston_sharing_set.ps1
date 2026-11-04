# Regenerate the four compact, borderless light-theme sharing posters with
# centered north markers and scale bars, using the latest public data.
# Run: pwsh -File scripts/render_boston_sharing_set.ps1
$ErrorActionPreference = 'Stop'
$renderer = Join-Path $PSScriptRoot 'render_boston_sources_map.py'
$frame = Join-Path $PSScriptRoot 'boston_sharing_frame.json'
foreach ($inset in @('mount_auburn', 'arboretum', 'harvard_square', 'back_bay_public_garden')) {
    $output = Join-Path $PSScriptRoot "boston_treelines_$inset.png"
    uv run $renderer --palette light --inset $inset --inset-canopy --north-shift-km 1.5 --frame-from $frame --compact --cartographic-details --output $output
    if ($LASTEXITCODE -ne 0) {
        throw "Rendering $inset failed (exit $LASTEXITCODE)."
    }
}
