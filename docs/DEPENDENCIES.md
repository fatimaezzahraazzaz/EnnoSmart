# Dépendances Python EnnoSmart

Date de l'audit : 15 septembre 2026  
Environnement principal : `C:\EnnoSmart\.venv`  
Source : `requirements.txt`

Les 59 dépendances ci-dessous sont les dépendances directes de production. Les
paquets installés automatiquement par pip (par exemple `scipy`, `tokenizers` ou
`urllib3`) sont des dépendances transitives et ne doivent pas être ajoutés à la
liste manuellement.

## API, configuration et sécurité

| Dépendance | Rôle dans EnnoSmart |
|---|---|
| `fastapi` | API REST, routes, validation et injection de dépendances. |
| `starlette` | Socle ASGI, réponses HTTP, middleware et gestion de concurrence. |
| `uvicorn[standard]` | Serveur ASGI qui lance le backend. |
| `anyio` | Limitation et exécution des tâches synchrones de l'API. |
| `typing-extensions` | Types Python récents employés par le code et les modèles. |
| `pydantic` | Validation et sérialisation des données métier/API. |
| `pydantic-settings` | Chargement typé de la configuration et des variables d'environnement. |
| `pydantic-ai-slim[openai]` | Sorties LLM structurées et adaptateur OpenAI. |
| `python-dotenv` | Lecture des fichiers `.env`. |
| `python-multipart` | Uploads de fichiers et formulaires FastAPI. |
| `email-validator` | Validation des champs `EmailStr` des comptes utilisateurs. |
| `python-jose[cryptography]` | Création et validation des jetons JWT. |
| `passlib[bcrypt]` | API de hachage et vérification des mots de passe. |
| `bcrypt` | Algorithme de hachage utilisé par Passlib, version compatible verrouillée. |

## Base de données, graphes et workers

| Dépendance | Rôle dans EnnoSmart |
|---|---|
| `SQLAlchemy` | ORM et accès PostgreSQL. |
| `psycopg2-binary` | Pilote PostgreSQL de SQLAlchemy et script d'initialisation. |
| `psycopg[binary,pool]` | Pilote et pool PostgreSQL modernes pour les checkpoints LangGraph. |
| `celery[redis]` | Exécution des traitements asynchrones EnnoScholar/CIR. |
| `kombu` | Déclaration des files, échanges et routage Celery. |
| `redis` | Broker, résultats, verrous distribués et contrôle de concurrence. |
| `langgraph` | Orchestration des workflows d'état de l'art. |
| `langgraph-checkpoint-postgres` | Persistance durable des états LangGraph dans PostgreSQL. |
| `langgraph-checkpoint-redis` | Sauvegarde des états des traitements CIR dans Redis. |

## HTTP, recherche scientifique et MCP

| Dépendance | Rôle dans EnnoSmart |
|---|---|
| `requests` | Requêtes HTTP synchrones vers les fournisseurs externes. |
| `httpx` | Requêtes HTTP asynchrones et clients de services. |
| `beautifulsoup4` | Analyse des pages HTML et découverte de texte intégral. |
| `lxml` | Analyse XML/HTML, notamment pour les documents Office. |
| `mcp[cli]` | Serveur MCP de récupération légale des publications. |
| `rapidfuzz` | Comparaisons textuelles floues pour la validation CIR. |
| `langfuse` | Traces et observabilité des workflows de recherche. |

## Documents, images et OCR

| Dépendance | Rôle dans EnnoSmart |
|---|---|
| `fpdf2` | Génération de fichiers PDF. |
| `PyMuPDF` | Lecture, rendu, surlignage et extraction rapide des PDF. |
| `pypdf` | Lecture PDF pure Python et repli d'extraction. |
| `pdfplumber` | Extraction structurée du texte et des tableaux PDF. |
| `Pillow` | Manipulation et conversion des images. |
| `CairoSVG` | Conversion des visuels SVG. |
| `pytesseract` | Interface Python vers l'OCR Tesseract. |
| `python-docx` | Lecture et génération DOCX. |
| `python-pptx` | Lecture des présentations PPTX. |
| `openpyxl` | Lecture des classeurs XLSX. |
| `xlrd` | Lecture des anciens classeurs XLS. |
| `extract-msg` | Extraction des courriels Outlook MSG. |
| `pandas` | Traitement des tableaux et données structurées. |

## NLP, embeddings et modèles

| Dépendance | Rôle dans EnnoSmart |
|---|---|
| `numpy` | Calcul numérique et manipulation des vecteurs. |
| `joblib` | Chargement des classifieurs NLP sérialisés. |
| `scikit-learn` | Pipelines/classifieurs NLP et support de Sentence Transformers. |
| `chromadb` | Index vectoriel central utilisé par le RAG et les mémoires. |
| `boto3` | Fournisseur S3 optionnel de Storage V2. |
| `sentence-transformers` | Embeddings, similarité et reranking sémantique. |
| `huggingface-hub` | Résolution et téléchargement contrôlé des modèles Hugging Face. |
| `torch` | Moteur d'exécution des modèles locaux. |
| `transformers` | Traduction OPUS-MT, reranking, vision et détecteurs IA. |
| `sentencepiece` | Tokenisation des modèles OPUS-MT. |
| `sacremoses` | Pré/post-traitement linguistique de la traduction OPUS-MT. |
| `lingua-language-detector` | Détection de langue. |
| `language-tool-python` | Contrôle grammatical de la rédaction scientifique. |

## Audio et Ollama

| Dépendance | Rôle dans EnnoSmart |
|---|---|
| `ctranslate2` | Runtime optimisé employé par Faster Whisper. |
| `faster-whisper` | Transcription locale des fichiers audio. |
| `ollama` | SDK du repli local pour la vision et les formules. |

## Capacités optionnelles non installées par défaut

`requirements-optional.txt` reste séparé : `surya-ocr` (OCR neuronal),
`pix2tex` (formules), `whisperx` (diarisation), `torchvision` et
`qwen-vl-utils` (vision Qwen). Ces paquets ne font pas partie du `.venv`
minimal tant que ces fonctions lourdes ne sont pas activées.

`accelerate` et l'ancien `PyPDF2` ont été retirés : aucun appel actif ne les
nécessite dans l'installation principale.
