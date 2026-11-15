# Richtet die Entwicklungsumgebung ein (außerhalb von OneDrive, damit nichts synchronisiert wird).
$venv = "$env:USERPROFILE\.venvs\wids-access-ai"
if (-not (Test-Path $venv)) { python -m venv $venv }
& "$venv\Scripts\python.exe" -m pip install --upgrade pip
& "$venv\Scripts\python.exe" -m pip install -e "$PSScriptRoot\..[dev]"
