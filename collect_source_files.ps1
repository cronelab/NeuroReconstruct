# collect_source_files.ps1
# Run from your project root: D:\Hopkins\CroneLab2\Projects\ReconstructionsProjectRevamped
# Copies all real source files into _upload folder for easy drag-select uploading
# .py files are renamed to .txt so Claude can read them

$outputDir = ".\\_upload"

if (Test-Path $outputDir) {
    Remove-Item $outputDir -Recurse -Force
}
New-Item -ItemType Directory -Path $outputDir | Out-Null

$excludeDirs = @("node_modules", "__pycache__", "data", ".git", "dist", "build", "_upload")
$includeExtensions = @(".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".json", ".txt", ".md")
$excludeFiles = @("brain_viewer.db", "project_tree.txt", "package-lock.json")

Get-ChildItem -Path "." -Recurse -File | Where-Object {
    $pathParts = $_.FullName -split "\\"
    $inExcludedDir = $false
    foreach ($part in $pathParts) {
        if ($excludeDirs -contains $part) {
            $inExcludedDir = $true
            break
        }
    }
    $isExcludedFile = $excludeFiles -contains $_.Name
    $hasValidExtension = $includeExtensions -contains $_.Extension.ToLower()
    -not $inExcludedDir -and -not $isExcludedFile -and $hasValidExtension
} | ForEach-Object {
    $relativePath = $_.FullName.Substring((Get-Location).Path.Length + 1)
    $flatName = $relativePath -replace "\\", "__"

    # Rename .py files to .txt so Claude can read them
    if ($_.Extension.ToLower() -eq ".py") {
        $flatName = $flatName + ".txt"
    }

    $destination = Join-Path $outputDir $flatName
    Copy-Item $_.FullName -Destination $destination
    Write-Host "Copied: $relativePath"
}

Write-Host ""
Write-Host "Done! Files saved to $outputDir"
Write-Host "Total files: $((Get-ChildItem $outputDir).Count)"
