from __future__ import annotations

import os

from celery import Celery
from kombu import Exchange, Queue

from backend_api.workers.cir_runtime_config_v3214 import (
    CIR_BROKER_URL,
    CIR_QUEUE,
    CIR_RESULT_BACKEND,
)

VISIBILITY_TIMEOUT = int(
    os.getenv(
        "ENNOSMART_CIR_VISIBILITY_TIMEOUT",
        "28800",
    )
)

# V3.21.5 : contrat de routage unique, explicite et partagé par producer/worker.
CIR_EXCHANGE = CIR_QUEUE
CIR_ROUTING_KEY = CIR_QUEUE

cir_exchange = Exchange(
    CIR_EXCHANGE,
    type="direct",
    durable=True,
)

cir_queue = Queue(
    CIR_QUEUE,
    exchange=cir_exchange,
    routing_key=CIR_ROUTING_KEY,
    durable=True,
)

celery_app = Celery(
    "ennosmart_cir",
    broker=CIR_BROKER_URL,
    backend=CIR_RESULT_BACKEND,
    include=[
        "backend_api.workers.cir_tasks_v321"
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Contrat AMQP explicite.
    task_queues=(cir_queue,),
    task_default_queue=CIR_QUEUE,
    task_default_exchange=CIR_EXCHANGE,
    task_default_exchange_type="direct",
    task_default_routing_key=CIR_ROUTING_KEY,
    task_create_missing_queues=False,
    task_routes={
        "ennosmart.cir.run_full_cir": {
            "queue": CIR_QUEUE,
            "exchange": CIR_EXCHANGE,
            "routing_key": CIR_ROUTING_KEY,
        }
    },

    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": VISIBILITY_TIMEOUT,
    },
    result_backend_transport_options={
        "visibility_timeout": VISIBILITY_TIMEOUT,
        "global_keyprefix": "ennosmart:cir:celery:",
        "retry_policy": {"timeout": 5.0},
    },
    result_expires=int(
        os.getenv(
            "ENNOSMART_CIR_RESULT_EXPIRES",
            "86400",
        )
    ),
)

print(
    "[V3.21.5][CeleryConfig] "
    f"broker={CIR_BROKER_URL} "
    f"results={CIR_RESULT_BACKEND} "
    f"queue={CIR_QUEUE} "
    f"exchange={CIR_EXCHANGE} "
    f"routing_key={CIR_ROUTING_KEY}"
)
