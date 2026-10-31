"""Honest user outcomes from attested runtime facts, never historical bibliography."""


def provider_failure(exc):
    status = getattr(exc,'status_code',None)
    return {'code':type(exc).__name__, 'http_status':status if isinstance(status,int) else None,
        'retryable':False}  # A human controls the next paid attempt.


def stopped(reason, usage, failure=None, refusal=None):
    failure,refusal = failure or {},refusal or {}
    action = ''
    if failure:
        code,status = failure['code'],failure.get('http_status')
        if status==401 or code=='AuthenticationError':
            outcome,answer = 'provider_authentication_error','Erreur fournisseur HTTP 401 : authentification refusée. Vérifiez la configuration de la clé avant toute nouvelle tentative.'
        elif 'Timeout' in code:
            outcome,answer = 'provider_timeout','Le délai de réponse du fournisseur est dépassé. La consommation non attestée reste réservée ; aucun nouvel appel automatique.'
            action = 'Un rapprochement de cet appel est nécessaire avant de reprendre cette exécution.'
        else:
            outcome,answer = 'provider_error',f'Appel fournisseur interrompu ({code}'+(f', HTTP {status}' if status else '')+'). Aucun nouvel appel automatique.'
            action = 'Vérifiez le service fournisseur avant de demander une reprise ; un appel encore incertain doit être rapproché.'
    elif reason in {'admission_refused','synthesis_reserve','section_save_reserve'}:
        outcome = 'reservation_insufficient'
        answer = 'Appel non envoyé : les ressources restantes ne permettent pas sa réservation et la sauvegarde requise.'
        action = 'Il peut rester des ressources, mais pas assez pour autoriser la prochaine opération. La reprise ne peut pas contourner cette réservation.'
    elif reason=='job_budget':
        limits = usage['limits']
        if refusal.get('axis')=='seconds' or usage.get('remaining_seconds',1)<=0:
            outcome,answer = 'time_exhausted','Le délai global est atteint.'
        elif refusal.get('axis')=='tokens' or usage['input_tokens']+usage['output_tokens']+usage.get('uncertain_tokens',0)>=limits['tokens']:
            outcome,answer = 'tokens_exhausted','Le plafond global de tokens engagés est atteint.'
        elif refusal.get('axis')=='web':
            outcome,answer = 'web_exhausted','Le plafond global du Web est atteint pour l’opération demandée.'
        elif refusal.get('axis')=='tools' or usage['tools']>=limits['tools']:
            outcome,answer = 'tools_exhausted','Le plafond global des outils est atteint.'
        else:
            outcome,answer = 'budget_insufficient','Le plafond financier en dollars est atteint par les engagements de cette exécution.'
        action = ('Cette exécution ne peut pas réinitialiser ses plafonds. Toute poursuite reste soumise aux ressources '
            'autorisées de la mission ou à une nouvelle demande explicite.')
        if outcome=='web_exhausted':
            action += ' Les outils locaux restent utilisables si les autres limites le permettent.'
    elif reason=='stagnation':
        outcome,answer = 'stagnation','Travail partiellement terminé : les dernières opérations n’apportaient plus de progrès.'
        action = 'Vous pouvez préciser un nouvel axe utile ou demander une reprise explicite dans les ressources restantes.'
    elif reason=='cancelled':
        outcome,answer = 'cancelled','Interruption demandée : l’exécution a été arrêtée.'
        action = 'Vous pouvez demander une reprise explicite, sous réserve des ressources et des appels encore incertains.'
    elif reason=='uncertain_model_call':
        outcome,answer = 'uncertain_model_call','Un appel fournisseur reste incertain ; ses ressources restent engagées.'
        action = 'Un rapprochement est nécessaire avant de reprendre cette exécution ; aucun appel n’est répété automatiquement.'
    elif reason=='uncertain_tool_call':
        outcome,answer = 'uncertain_tool_call','Une opération a été interrompue avant confirmation de son résultat.'
        action = 'Il faut vérifier son résultat avant toute reprise ; aucune sauvegarde de cette opération n’est annoncée comme confirmée.'
    elif reason=='finalization_failed':
        outcome,answer = 'finalization_failed','La validation de la réponse a échoué ; cette réponse n’est pas publiée comme résultat scientifique validé.'
        action = 'Examinez les motifs de refus avant de demander une correction.'
    elif reason in {'provider_protocol_arguments','provider_failed'} or str(reason).startswith('provider_error:'):
        outcome,answer = 'provider_error','Erreur technique fournisseur : le format ou le statut de la réponse reçue ne permet pas sa validation.'
        action = 'Vérifiez le fournisseur avant une nouvelle tentative ; les éventuels appels incertains doivent être rapprochés.'
    elif reason in {'consultant_review','model_requested_pause','requested_pause'}:
        outcome,answer = 'requested_pause','Travail en pause pour examen par le consultant.'
        action = 'Vous pouvez demander une reprise explicite dans les ressources restantes.'
    elif reason=='worker_interrupted' or str(reason).startswith('v5_interrupted:'):
        outcome,answer = 'worker_interrupted','Exécution V5 interrompue par un incident technique.'
        action = ('Demandez une reprise explicite avec le même budget ; sa possibilité est vérifiée avant tout appel. '
            'Un appel fournisseur encore incertain doit d’abord être rapproché.')
    else:
        return None
    diagnostic = failure or {'retryable':False,'reason':reason,**refusal}
    return {'answer':answer+' Les résultats déjà sauvegardés et les tâches restantes sont conservés.'+(' '+action if action else ''),
        'citations':[], 'partial':True, 'coverage_sufficient':False, 'outcome':outcome,
        **({'diagnostic':diagnostic} if failure or reason in
            {'job_budget','admission_refused','synthesis_reserve','section_save_reserve'} else {})}


def with_notice(result,notice):
    """Keep a current, saved result alongside its stop cause, never old chat science."""
    if not notice:
        return result
    answer = result.get('answer','')
    return {**result,**notice, 'answer':notice['answer']+ ('\n\n'+answer if answer and answer!=notice['answer'] else ''),
        'citations':result.get('citations',[])}
