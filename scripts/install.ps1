param(
    [switch]$Dev
)

$ErrorActionPreference = "Stop"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $condaPython = Join-Path $env:USERPROFILE "anaconda3\python.exe"
    if (Test-Path $condaPython) {
        $python = @{ Source = $condaPython }
    }
}
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $python) {
    throw "No encontre python, py ni Anaconda en la ruta esperada. Activa tu entorno o usa Anaconda Prompt."
}

if ($Dev) {
    & $python.Source -m pip install -e ".[dev,notebook]"
} else {
    & $python.Source -m pip install -e .
}
