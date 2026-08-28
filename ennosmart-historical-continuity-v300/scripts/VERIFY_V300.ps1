$m = Get-Content "C:\EnnoSmart\agents\EnnoDiagnostic\historical_continuity_reconciler.py" -Raw
$a = Get-Content "C:\EnnoSmart\agents\EnnoDiagnostic\ennodiagnostic_agent.py" -Raw
@(
  "historical_continuity_reconciler_v300",
  "_reconstruct_historical_families_with_llm_v300",
  "canonical_current_title",
  "_normative_only_candidate_v300"
) | ForEach-Object {
  if ($m.Contains($_)) { Write-Host "[OK] $_" -ForegroundColor Green }
  else { throw "Missing $_" }
}
if (!$a.Contains('"historical_continuity_report": historical_continuity_report')) { throw "Agent integration missing" }
Write-Host "V300 OK" -ForegroundColor Green
