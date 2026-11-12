# Record with: vhs scripts/demo.tape
# Requires vhs (https://github.com/charmbracelet/vhs), ttyd, and ffmpeg.
Output docs/demo.gif

Set Shell "powershell"
Set FontSize 14
Set Width 1280
Set Height 800
Set Padding 20
Set TypingSpeed 60ms

Type "docker compose ps"
Enter
Sleep 3s

Type "uv run python scripts/smoke_ingest.py"
Enter
Sleep 8s

Type "uv run python scripts/smoke_delivery.py"
Enter
Sleep 12s

Type "uv run python scripts/smoke_replay.py"
Enter
Sleep 15s

Type "curl.exe -s http://localhost:8000/metrics | Select-String hookrelay_ingest_total"
Enter
Sleep 4s
