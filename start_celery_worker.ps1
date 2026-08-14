Set-Location "C:\EnnoSmart\backend_api"

# Lanceur compatible Windows : il prépare lui-même les chemins Python du
# backend et évite les erreurs tardives « No module named db ».
& "C:\EnnoSmart\.venv_py314\Scripts\python.exe" `
    "C:\EnnoSmart\backend_api\worker\run_local_worker.py" `
    "ennoscholar@%h" `
    "celery"
