Set-Location "$PSScriptRoot\.."
& "$env:USERPROFILE\.venvs\wids-access-ai\Scripts\python.exe" -m pytest tests @args
