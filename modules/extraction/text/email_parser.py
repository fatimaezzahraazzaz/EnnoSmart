"""
modules/extraction/text/email_parser.py
──────────────────────────────────────────────────────────────────────────────
Extraction d'emails pour dossiers R&D / CIR.

Formats supportés :
  - .eml  : format standard RFC 2822 (Thunderbird, Gmail export, etc.)
  - .msg  : format propriétaire Microsoft Outlook

Stratégie :
  - Extraction du corps (texte brut prioritaire, HTML en fallback)
  - Extraction des métadonnées (expéditeur, destinataires, date, objet)
  - Reconstruction du fil de discussion (reply chain) → chunks ordonnés
  - Extraction des pièces jointes → liste pour routage vers autres extracteurs
  - Détection de l'organisme expéditeur via domaine email + contenu
  - Détection de contenu R&D / CIR dans le corps
  - Nettoyage des signatures et disclaimers répétitifs

"""

from __future__ import annotations

import email
import email.policy
import logging
import re
import quopri
import base64
from dataclasses import dataclass, field
from datetime import datetime
from email.header import decode_header
from enum import Enum
from pathlib import Path
from typing import Optional

# ── Import .msg (Outlook) ─────────────────────────────────────────────────────
try:
    import extract_msg
    EXTRACT_MSG_AVAILABLE = True
except ImportError:
    EXTRACT_MSG_AVAILABLE = False

# ── Import HTML → texte ───────────────────────────────────────────────────────
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

from modules.extraction.formula.formula import extract_formulas

logger = logging.getLogger(__name__)


# ── Constantes ────────────────────────────────────────────────────────────────

# Domaines d'organismes connus dans l'écosystème R&D français
ORGANISME_DOMAINS: dict[str, str] = {
    "bpifrance.fr":          "BPI_FRANCE",
    "bpi.fr":                "BPI_FRANCE",
    "anrt.asso.fr":          "ANRT",
    "anr.fr":                "ANR",
    "dgfip.finances.gouv.fr":"DRFIP",
    "finances.gouv.fr":      "DRFIP",
    "recherche.gouv.fr":     "MINISTERE",
    "cnrs.fr":               "LABORATOIRE",
    "inria.fr":              "LABORATOIRE",
    "cea.fr":                "LABORATOIRE",
    "inrae.fr":              "LABORATOIRE",
    "inserm.fr":             "LABORATOIRE",
}

# Patterns de signatures email à supprimer (bruit pour le RAG)
SIGNATURE_PATTERNS: list[str] = [
    r"--\s*\n.*",                               # Signature standard "--"
    r"_{3,}.*",                                 # Ligne de séparation ___
    r"(cordialement|bien cordialement|"
    r"sincèrement|regards|best regards)"
    r"[\s\S]{0,300}$",                          # Formule de politesse + fin
    r"(ce message.*confidentiel[\s\S]{0,500})", # Disclaimer confidentialité
    r"(this e?-?mail.*confidential[\s\S]{0,500})",
    r"(veuillez ne pas imprimer[\s\S]{0,200})", # Message écologique
]

_SIGNATURE_RE = re.compile(
    "|".join(SIGNATURE_PATTERNS),
    flags=re.IGNORECASE | re.DOTALL,
)

# Patterns de lignes de citation dans les reply chains
QUOTE_LINE_PATTERNS: list[str] = [
    r"^>+\s?.*$",                              # > citation standard
    r"^De\s*:.*$",                             # En-tête reply FR
    r"^From\s*:.*$",                           # En-tête reply EN
    r"^Le\s+\w+.*a écrit\s*:$",               # "Le lundi X a écrit :"
    r"^On\s+\w+.*wrote\s*:$",                 # "On Mon X wrote:"
    r"^-{3,}\s*(Message original|Original Message)\s*-{3,}$",
]

_QUOTE_LINE_RE = re.compile(
    "|".join(QUOTE_LINE_PATTERNS),
    flags=re.IGNORECASE | re.MULTILINE,
)

# Extensions de pièces jointes pertinentes R&D.
# Important : les formats macro-enabled Office (.docm, .pptm, .xlsm)
# doivent être conservés pour le routeur d'extraction.
RD_ATTACHMENT_EXTENSIONS = {
    ".pdf",

    ".doc",
    ".docx",
    ".docm",

    ".ppt",
    ".pptx",
    ".pptm",

    ".xls",
    ".xlsx",
    ".xlsm",

    ".csv",
    ".txt",
    ".md",
    ".json",

    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".bmp",
    ".gif",
    ".webp",

    ".zip",
    ".rar",
    ".7z",
}

# Limites de sécurité pour éviter d'embarquer des pièces jointes énormes en mémoire.
MAX_ATTACHMENT_BYTES = 80 * 1024 * 1024  # 80 MB

# Sections R&D standards
RD_SECTION_PATTERNS: list[str] = [
    r"(objectif[s]?\s+(?:du\s+)?projet)",
    r"(état\s+de\s+l['\s]art)",
    r"(verrous?\s+technologique[s]?)",
    r"(travaux\s+(?:de\s+)?r(?:echerche)?(?:\s*&\s*|\s+et\s+)d(?:éveloppement)?)",
    r"(résultats?\s+(?:obtenus?|attendus?))",
    r"(compte.rendu|cr\s+réunion|procès.verbal)",
    r"(dépenses?\s+(?:de\s+)?recherche)",
    r"(avancement\s+(?:du\s+)?projet)",
    r"(livrable[s]?)",
    r"(jalon[s]?|milestone[s]?)",
]

_RD_SECTION_RE = re.compile(
    "|".join(RD_SECTION_PATTERNS),
    flags=re.IGNORECASE | re.UNICODE,
)


# ── Enums ─────────────────────────────────────────────────────────────────────

class EmailFormat(str, Enum):
    EML = "eml"
    MSG = "msg"


class EmailDirection(str, Enum):
    INBOUND  = "inbound"     # Reçu par l'équipe projet
    OUTBOUND = "outbound"    # Envoyé par l'équipe projet
    INTERNAL = "internal"    # Interne à l'organisation
    UNKNOWN  = "unknown"


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class EmailAttachment:
    """Pièce jointe détectée dans l'email."""
    filename: str
    extension: str
    size_bytes: int
    content_type: str
    is_rd_relevant: bool        # Extension pertinente pour R&D
    content: Optional[bytes] = None   # Contenu brut (pour routage extraction)


@dataclass
class EmailParticipant:
    """Expéditeur ou destinataire d'un email."""
    display_name: Optional[str]
    email_address: str
    domain: str
    organisme: Optional[str]    # Détecté depuis le domaine

@dataclass
class ReplyMessage:
    """Un message dans le fil de discussion (reply chain)."""
    position: int               # 0 = message le plus récent
    sender: Optional[str]
    date_str: Optional[str]
    body: str
    char_count: int


@dataclass
class EmailMetadata:
    """Métadonnées complètes de l'email."""
    subject: Optional[str]              = None
    sender: Optional[EmailParticipant]  = None
    recipients_to: list[EmailParticipant]  = field(default_factory=list)
    recipients_cc: list[EmailParticipant]  = field(default_factory=list)
    date: Optional[datetime]            = None
    date_str: Optional[str]             = None
    message_id: Optional[str]          = None
    in_reply_to: Optional[str]         = None
    thread_topic: Optional[str]        = None
    organisme_detected: Optional[str]  = None
    direction: EmailDirection           = EmailDirection.UNKNOWN
    is_reply: bool                      = False
    has_attachments: bool               = False


@dataclass
class EmailResult:
    """
    Résultat complet d'extraction d'un email.
    Compatible avec ExtractionResult (base.py).
    """
    file_name: str
    source_path: str
    file_type: str = "email"

    # ── Sortie principale pour le RAG ──────────────────────────────────────
    text_chunks: list[str] = field(default_factory=list)
    # Chunk 0 : message principal (nettoyé)
    # Chunk 1..N : messages du fil de discussion si reply chain

    # ── Corps brut nettoyé ─────────────────────────────────────────────────
    body_clean: str = ""

    # ── Fil de discussion ──────────────────────────────────────────────────
    reply_chain: list[ReplyMessage] = field(default_factory=list)

    # ── Pièces jointes ─────────────────────────────────────────────────────
    attachments: list[EmailAttachment] = field(default_factory=list)

    # ── Métadonnées ────────────────────────────────────────────────────────
    metadata: EmailMetadata = field(default_factory=EmailMetadata)

    # ── Sections R&D détectées ─────────────────────────────────────────────
    detected_rd_sections: list[str] = field(default_factory=list)

    # ── Traçabilité ────────────────────────────────────────────────────────
    tags: list[str] = field(default_factory=list)
    confidence_score: float = 1.0
    extraction_errors: list[str] = field(default_factory=list)


# ── Utilitaires ───────────────────────────────────────────────────────────────

def _decode_mime_header(value: Optional[str]) -> str:
    """
    Décode un en-tête MIME encodé (ex: =?UTF-8?B?...?= ou =?iso-8859-1?Q?...?=).
    Retourne une chaîne Unicode propre.
    """
    if not value:
        return ""
    try:
        parts = decode_header(value)
        decoded_parts: list[str] = []
        for raw, charset in parts:
            if isinstance(raw, bytes):
                enc = charset or "utf-8"
                try:
                    decoded_parts.append(raw.decode(enc, errors="replace"))
                except LookupError:
                    decoded_parts.append(raw.decode("utf-8", errors="replace"))
            else:
                decoded_parts.append(str(raw))
        return " ".join(decoded_parts).strip()
    except Exception:
        return str(value).strip()



def _safe_attachment_filename(filename: Optional[str], fallback: str = "attachment.bin") -> str:
    """
    Nettoie le nom d'une pièce jointe pour écriture temporaire côté router.
    Ne garde pas de chemin fourni par le mail.
    """
    name = _decode_mime_header(filename or "").strip() or fallback
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]+", "_", name)
    name = re.sub(r"[<>:\"/\\|?*]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or fallback


def _is_rd_attachment_extension(ext: str) -> bool:
    return str(ext or "").lower().strip() in RD_ATTACHMENT_EXTENSIONS


def _bytes_from_msg_attachment(attachment) -> bytes:
    """
    Récupère le contenu binaire d'une pièce jointe Outlook .msg.
    extract-msg varie selon les versions : data peut être bytes, property, None, etc.
    """
    for attr in ("data", "content", "dataBytes", "data_bytes"):
        try:
            value = getattr(attachment, attr, None)
            if callable(value):
                value = value()
            if isinstance(value, bytes):
                return value
            if isinstance(value, bytearray):
                return bytes(value)
        except Exception:
            continue

    # Certains objets extract_msg exposent un flux OLE interne.
    try:
        if hasattr(attachment, "save"):
            import tempfile
            tmp_dir = Path(tempfile.mkdtemp(prefix="ennosmart_msg_att_read_"))
            attachment.save(customPath=str(tmp_dir))
            files = [p for p in tmp_dir.rglob("*") if p.is_file()]
            if files:
                return files[0].read_bytes()
    except Exception:
        pass

    return b""


def _make_attachment(
    filename: str,
    content_type: str,
    payload: bytes,
) -> EmailAttachment:
    """
    Construit une pièce jointe normalisée et traçable.
    """
    safe_name = _safe_attachment_filename(filename)
    ext = Path(safe_name).suffix.lower()
    payload = payload or b""
    is_relevant = _is_rd_attachment_extension(ext)

    if len(payload) > MAX_ATTACHMENT_BYTES:
        logger.warning(
            "Pièce jointe ignorée car trop volumineuse : %s | %.2f MB",
            safe_name,
            len(payload) / (1024 * 1024),
        )
        return EmailAttachment(
            filename=safe_name,
            extension=ext,
            size_bytes=len(payload),
            content_type=content_type or "application/octet-stream",
            is_rd_relevant=False,
            content=None,
        )

    return EmailAttachment(
        filename=safe_name,
        extension=ext,
        size_bytes=len(payload),
        content_type=content_type or "application/octet-stream",
        is_rd_relevant=is_relevant,
        content=payload if is_relevant and payload else None,
    )


def _parse_email_address(raw: str) -> EmailParticipant:
    """
    Parse une adresse email brute en EmailParticipant.
    Gère les formats : "Nom Prénom <email@domain.com>" et "email@domain.com"
    """
    raw = _decode_mime_header(raw).strip()

    # Format "Nom <email>"
    match = re.search(r"<([^>]+)>", raw)
    if match:
        address = match.group(1).strip().lower()
        display_name = raw[:match.start()].strip().strip('"\'')
    else:
        address = raw.strip().lower()
        display_name = None

    # Domaine
    domain = address.split("@")[-1] if "@" in address else ""

    # Organisme depuis domaine
    organisme = None
    for dom, org in ORGANISME_DOMAINS.items():
        if domain.endswith(dom):
            organisme = org
            break

    return EmailParticipant(
        display_name=display_name or None,
        email_address=address,
        domain=domain,
        organisme=organisme,
    )


def _parse_participants(header_value: str) -> list[EmailParticipant]:
    """Parse une liste d'adresses email séparées par des virgules."""
    if not header_value:
        return []
    # Sépare sur les virgules qui ne sont pas dans des <...>
    parts = re.split(r",(?![^<]*>)", header_value)
    return [_parse_participant(p) for p in parts if p.strip()]


def _parse_participant(raw: str) -> EmailParticipant:
    return _parse_email_address(raw)


def _html_to_text(html: str) -> str:
    """
    Convertit le HTML d'un email en texte brut lisible.
    Préserve la structure (paragraphes, listes) pour le RAG.
    """
    if not BS4_AVAILABLE:
        # Fallback : suppression naïve des balises HTML
        text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
        text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<li[^>]*>", "\n• ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    soup = BeautifulSoup(html, "html.parser")

    # Supprimer les scripts, styles, et balises non-contenu
    for tag in soup(["script", "style", "head", "meta", "link"]):
        tag.decompose()

    # Préserver les sauts de ligne
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for p in soup.find_all(["p", "div", "tr"]):
        p.append("\n")
    for li in soup.find_all("li"):
        li.insert(0, "• ")
        li.append("\n")

    text = soup.get_text(separator=" ")
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clean_body(text: str) -> str:
    """
    Nettoie le corps d'un email pour le RAG.

      1. Suppression des signatures
      2. Suppression des disclaimers
      3. Normalisation des espaces/sauts de ligne
      4. Conservation du contenu métier
    """
    if not text:
        return ""

    # Supprimer les signatures et disclaimers
    cleaned = _SIGNATURE_RE.sub("", text)

    # Normaliser les sauts de ligne multiples
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    # Supprimer les lignes vides en début/fin
    cleaned = cleaned.strip()

    return cleaned


def _split_reply_chain(body: str) -> list[str]:
    """
    Détecte et sépare les messages dans un fil de discussion.

    Retourne une liste ordonnée du plus récent au plus ancien :
      [message_principal, reply_1, reply_2
    """
    # Séparateurs de reply chain
    separators = re.compile(
        r"(?:^|\n)"
        r"(?:-{3,}\s*(?:Message original|Original Message)\s*-{3,}"
        r"|^De\s*:.*\nEnvoyé\s*:.*\nÀ\s*:.*\nObjet\s*:.*"
        r"|^From\s*:.*\nSent\s*:.*\nTo\s*:.*\nSubject\s*:.*"
        r"|^Le\s+\S+.*a écrit\s*:\s*$"
        r"|^On\s+\S+.*wrote\s*:\s*$)",
        flags=re.IGNORECASE | re.MULTILINE,
    )

    parts = separators.split(body)
    # Filtrer les parties vides ou trop courtes
    messages = [p.strip() for p in parts if p and len(p.strip()) > 30]
    return messages


def _detect_rd_sections(text: str) -> list[str]:
    matches = _RD_SECTION_RE.findall(text)
    sections = []
    for match in matches:
        if isinstance(match, tuple):
            sections.extend(s.strip() for s in match if s.strip())
        elif match.strip():
            sections.append(match.strip())
    seen: set[str] = set()
    unique: list[str] = []
    for s in sections:
        key = s.lower()
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


def _detect_direction(metadata: EmailMetadata) -> EmailDirection:
    """
    Détermine si l'email est entrant, sortant ou interne.
    Basé sur les domaines expéditeur/destinataires.
    """
    if not metadata.sender:
        return EmailDirection.UNKNOWN

    sender_domain = metadata.sender.domain
    recipient_domains = {
        r.domain for r in metadata.recipients_to + metadata.recipients_cc
        if r.domain
    }

    # Si l'expéditeur est un organisme officiel → entrant
    if metadata.sender.organisme in {"BPI_FRANCE", "ANRT", "DRFIP", "ANR"}:
        return EmailDirection.INBOUND

    # Si tous les domaines sont identiques → interne
    all_domains = {sender_domain} | recipient_domains
    if len(all_domains) == 1:
        return EmailDirection.INTERNAL

    return EmailDirection.UNKNOWN


def _build_email_chunk(
    body: str,
    metadata: EmailMetadata,
    position: int = 0,
) -> str:
    """
    Assemble le chunk RAG d'un email avec intégration des formules.

    Format :
        [EMAIL | 2024-03-15 | De: nom@domain.com | Objet: Titre]
        [FIL: position 1/3]   ← si reply chain
        <corps nettoyé>
        
        [FORMULES DÉTECTÉES]
        1. LaTeX: ...
           Explication: ...
    """
    date_str = metadata.date_str or "date inconnue"
    sender_str = (
        metadata.sender.email_address
        if metadata.sender else "expéditeur inconnu"
    )
    subject_str = metadata.subject or "sans objet"

    header = (
        f"[EMAIL | {date_str} | "
        f"De: {sender_str} | "
        f"Objet: {subject_str}]"
    )

    if position > 0:
        header += f"\n[FIL DE DISCUSSION — MESSAGE {position + 1}]"

    chunk = f"{header}\n\n{body.strip()}"
    
    
    return chunk


# ══════════════════════════════════════════════════════════════════════════════
# Extraction EML
# ══════════════════════════════════════════════════════════════════════════════

def _extract_body_from_eml(msg: email.message.Message) -> str:
    """
    Extrait le corps textuel d'un message email.
    Priorité : text/plain > text/html (converti)
    Gère le multipart récursif.
    """
    plain_body = ""
    html_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))

            # Ignorer les pièces jointes
            if "attachment" in disposition:
                continue

            payload = part.get_payload(decode=True)
            if not payload:
                continue

            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except Exception:
                text = payload.decode("utf-8", errors="replace")

            if ctype == "text/plain" and not plain_body:
                plain_body = text
            elif ctype == "text/html" and not html_body:
                html_body = text
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except Exception:
                text = payload.decode("utf-8", errors="replace")

            if msg.get_content_type() == "text/html":
                html_body = text
            else:
                plain_body = text

    # Priorité au texte brut, fallback HTML converti
    if plain_body.strip():
        return plain_body
    elif html_body.strip():
        return _html_to_text(html_body)
    return ""


def _extract_attachments_eml(
    msg: email.message.Message,
) -> list[EmailAttachment]:
    """
    Extrait les pièces jointes d'un .eml avec contenu binaire.
    Les pièces jointes non pertinentes sont gardées en métadonnées,
    mais leur contenu n'est pas transmis au routeur.
    """
    attachments: list[EmailAttachment] = []

    for part in msg.walk():
        disposition = str(part.get("Content-Disposition", "") or "").lower()
        filename_raw = part.get_filename()

        # Certains emails mettent les fichiers en inline avec un filename.
        if "attachment" not in disposition and not filename_raw:
            continue

        if not filename_raw:
            continue

        filename = _safe_attachment_filename(filename_raw)
        payload = part.get_payload(decode=True) or b""
        content_type = part.get_content_type() or "application/octet-stream"

        att = _make_attachment(
            filename=filename,
            content_type=content_type,
            payload=payload,
        )

        attachments.append(att)

        logger.info(
            "PJ EML détectée : %s | ext=%s | RD=%s | size=%d",
            att.filename,
            att.extension,
            att.is_rd_relevant,
            att.size_bytes,
        )

    return attachments


def _parse_eml_date(date_str: str) -> tuple[Optional[datetime], str]:
    """
    Parse la date d'un email EML.
    Retourne (datetime_obj, str_formatée).
    """
    if not date_str:
        return None, ""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        return dt, dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None, date_str.strip()


def _extract_eml(path: Path) -> EmailResult:
    """Pipeline complet d'extraction .eml."""
    result = EmailResult(
        file_name=path.name,
        source_path=str(path.resolve()),
        file_type=EmailFormat.EML.value,
    )

    try:
        with open(path, "rb") as f:
            msg = email.message_from_binary_file(f, policy=email.policy.default)
    except Exception as exc:
        result.extraction_errors.append(f"Impossible de lire le .eml : {exc}")
        return result

    # ── Métadonnées ───────────────────────────────────────────────────────
    subject = _decode_mime_header(msg.get("Subject", ""))
    from_raw = _decode_mime_header(msg.get("From", ""))
    to_raw = msg.get("To", "")
    cc_raw = msg.get("Cc", "")
    date_raw = msg.get("Date", "")
    message_id = msg.get("Message-ID", "").strip()
    in_reply_to = msg.get("In-Reply-To", "").strip()
    thread_topic = _decode_mime_header(msg.get("Thread-Topic", ""))

    date_obj, date_str = _parse_eml_date(date_raw)

    sender = _parse_email_address(from_raw) if from_raw else None
    recipients_to = _parse_participants(to_raw)
    recipients_cc = _parse_participants(cc_raw)

    result.metadata = EmailMetadata(
        subject=subject or None,
        sender=sender,
        recipients_to=recipients_to,
        recipients_cc=recipients_cc,
        date=date_obj,
        date_str=date_str,
        message_id=message_id or None,
        in_reply_to=in_reply_to or None,
        thread_topic=thread_topic or None,
        is_reply=bool(in_reply_to),
    )

    # ── Corps ─────────────────────────────────────────────────────────────
    raw_body = _extract_body_from_eml(msg)

    # ── Pièces jointes ────────────────────────────────────────────────────
    result.attachments = _extract_attachments_eml(msg)
    result.metadata.has_attachments = bool(result.attachments)

    return result, raw_body


# ══════════════════════════════════════════════════════════════════════════════
# Extraction MSG (Outlook)
# ══════════════════════════════════════════════════════════════════════════════

def _extract_msg(path: Path) -> tuple["EmailResult", str]:
    """Pipeline complet d'extraction .msg (Outlook)."""
    result = EmailResult(
        file_name=path.name,
        source_path=str(path.resolve()),
        file_type=EmailFormat.MSG.value,
    )

    if not EXTRACT_MSG_AVAILABLE:
        result.extraction_errors.append(
            "extract-msg non installé : pip install extract-msg"
        )
        return result, ""

    try:
        msg = extract_msg.openMsg(str(path))
    except Exception as exc:
        result.extraction_errors.append(f"Impossible de lire le .msg : {exc}")
        return result, ""

    try:
        # ── Métadonnées ───────────────────────────────────────────────────
        subject = msg.subject or ""
        sender_raw = msg.sender or ""
        to_raw = msg.to or ""
        cc_raw = msg.cc or ""
        date_obj = msg.date
        date_str = date_obj.strftime("%Y-%m-%d %H:%M") if date_obj else ""
        message_id = getattr(msg, "messageId", "") or ""
        in_reply_to = getattr(msg, "inReplyTo", "") or ""

        sender = _parse_email_address(sender_raw) if sender_raw else None
        recipients_to = _parse_participants(to_raw)
        recipients_cc = _parse_participants(cc_raw)

        result.metadata = EmailMetadata(
            subject=subject or None,
            sender=sender,
            recipients_to=recipients_to,
            recipients_cc=recipients_cc,
            date=date_obj,
            date_str=date_str,
            message_id=message_id or None,
            in_reply_to=in_reply_to or None,
            is_reply=bool(in_reply_to),
        )

        # ── Corps ─────────────────────────────────────────────────────────
        # Priorité : texte brut > HTML converti
        raw_body = ""
        if msg.body:
            raw_body = msg.body
        elif msg.htmlBody:
            html = msg.htmlBody
            if isinstance(html, bytes):
                html = html.decode("utf-8", errors="replace")
            raw_body = _html_to_text(html)

        # ── Pièces jointes ────────────────────────────────────────────────
        for attachment in (msg.attachments or []):
            try:
                filename = (
                    getattr(attachment, "longFilename", None)
                    or getattr(attachment, "shortFilename", None)
                    or getattr(attachment, "name", None)
                    or ""
                )

                filename = _safe_attachment_filename(filename)

                if not filename:
                    continue

                data = _bytes_from_msg_attachment(attachment)
                content_type = (
                    getattr(attachment, "mimetype", None)
                    or getattr(attachment, "contentType", None)
                    or "application/octet-stream"
                )

                att = _make_attachment(
                    filename=filename,
                    content_type=content_type,
                    payload=data,
                )

                result.attachments.append(att)

                logger.info(
                    "PJ MSG détectée : %s | ext=%s | RD=%s | size=%d",
                    att.filename,
                    att.extension,
                    att.is_rd_relevant,
                    att.size_bytes,
                )

            except Exception as exc:
                logger.warning("Pièce jointe MSG ignorée : %s", exc)

        result.metadata.has_attachments = bool(result.attachments)

    finally:
        try:
            msg.close()
        except Exception:
            pass

    return result, raw_body


# ══════════════════════════════════════════════════════════════════════════════
# Post-traitement commun
# ══════════════════════════════════════════════════════════════════════════════

def _postprocess(result: EmailResult, raw_body: str) -> EmailResult:
    """
    Post-traitement commun EML / MSG :
      - Nettoyage du corps
      - Séparation du fil de discussion
      - Construction des chunks RAG
      - Détection organisme et direction
      - Sections R&D
    """
    if not raw_body.strip():
        result.body_clean = ""
        result.text_chunks.append(
            _build_email_chunk("[CORPS VIDE]", result.metadata)
        )
        result.confidence_score = 0.20
        return result

    # ── Nettoyage ─────────────────────────────────────────────────────────
    body_clean = _clean_body(raw_body)
    result.body_clean = body_clean

    # ── Fil de discussion ─────────────────────────────────────────────────
    chain_parts = _split_reply_chain(body_clean)

    if len(chain_parts) > 1:
        # Email avec historique → un chunk par message
        for i, part in enumerate(chain_parts):
            part_clean = _clean_body(part)
            if len(part_clean.strip()) > 30:
                reply = ReplyMessage(
                    position=i,
                    sender=None,      # Difficile à extraire fiablement depuis le corps
                    date_str=None,
                    body=part_clean,
                    char_count=len(part_clean),
                )
                result.reply_chain.append(reply)
                result.text_chunks.append(
                    _build_email_chunk(part_clean, result.metadata, position=i)
                )
    else:
        # Email simple → un seul chunk
        result.text_chunks.append(
            _build_email_chunk(body_clean, result.metadata)
        )

    # ── Détection organisme ───────────────────────────────────────────────
    # ── Direction ─────────────────────────────────────────────────────────
    result.metadata.direction = _detect_direction(result.metadata)

    # ── Sections R&D ─────────────────────────────────────────────────────
    full_text = body_clean + " " + (result.metadata.subject or "")
    result.detected_rd_sections = _detect_rd_sections(full_text)

    return result


def _build_tags(result: EmailResult) -> list[str]:
    tags: list[str] = [f"EMAIL:{result.file_type.upper()}"]

    if result.metadata.is_reply or len(result.reply_chain) > 1:
        tags.append("REPLY_CHAIN")

    if result.metadata.has_attachments:
        tags.append("HAS_ATTACHMENTS")
        rd_attachments = [a for a in result.attachments if a.is_rd_relevant]
        if rd_attachments:
            tags.append("HAS_RD_ATTACHMENTS")

    if result.metadata.direction != EmailDirection.UNKNOWN:
        tags.append(f"DIR:{result.metadata.direction.value.upper()}")

    if result.detected_rd_sections:
        tags.append("CIR_CONTENT")

    if result.extraction_errors:
        tags.append("PARTIAL_EXTRACTION")

    return tags


def _compute_confidence(result: EmailResult) -> float:
    """
    Score de confiance basé sur la richesse des métadonnées et du corps.
    """
    score = 1.0

    if not result.body_clean.strip():
        return 0.20
    if not result.metadata.sender:
        score -= 0.10
    if not result.metadata.date_str:
        score -= 0.10
    if not result.metadata.subject:
        score -= 0.05
    score -= min(len(result.extraction_errors) * 0.10, 0.30)

    return max(round(score, 2), 0.10)


# ── Point d'entrée principal ──────────────────────────────────────────────────

def extract_email(file_path: str | Path) -> EmailResult:
    """
    Extrait le contenu d'un email (.eml ou .msg) pour le RAG EnnoSmart.

    Paramètres
    ----------
    file_path : str | Path
        Chemin vers le fichier .eml ou .msg

    Retourne
    --------
    EmailResult
        text_chunks          : chunks RAG (1 par message du fil de discussion)
        body_clean           : corps principal nettoyé
        reply_chain          : messages historiques séparés
        attachments          : pièces jointes détectées + contenu brut
        metadata             : expéditeur, destinataires, date, objet, organisme
        detected_rd_sections : sections CIR/R&D trouvées
        tags                 : traçabilité
        confidence_score     : qualité globale

    Raises
    ------
    FileNotFoundError : fichier introuvable
    ValueError        : extension non supportée
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {path}")

    ext = path.suffix.lower()
    if ext not in (".eml", ".msg"):
        raise ValueError(
            f"Extension '{ext}' non supportée par email_parser.py. "
            f"Formats acceptés : .eml, .msg"
        )

    logger.info("Extraction email [%s] → %s", ext.upper(), path.name)

    if ext == ".eml":
        result, raw_body = _extract_eml(path)
    else:
        result, raw_body = _extract_msg(path)

    # Post-traitement commun
    result = _postprocess(result, raw_body)
    result.tags = _build_tags(result)
    result.confidence_score = _compute_confidence(result)

    rd_attachments = [a for a in result.attachments if a.is_rd_relevant and a.content]

    logger.info(
        "✓ %s — %d chunks | %d pièces jointes | %d PJ R&D exploitables | score=%.2f | tags=%s",
        path.name,
        len(result.text_chunks),
        len(result.attachments),
        len(rd_attachments),
        result.confidence_score,
        result.tags,
    )

    logger.info("=" * 80)
    logger.info("EMAIL EXTRACTION DEBUG : %s", result.file_name)
    logger.info("Subject : %s", result.metadata.subject)
    logger.info("Body chars : %d", len(result.body_clean or ""))
    logger.info("Chunks : %d", len(result.text_chunks))
    logger.info("Attachments : %d", len(result.attachments))

    for att in result.attachments:
        logger.info(
            " - PJ : %s | ext=%s | RD=%s | content=%s | size=%d | type=%s",
            att.filename,
            att.extension,
            att.is_rd_relevant,
            bool(att.content),
            att.size_bytes,
            att.content_type,
        )

    logger.info("=" * 80)

    return result


# ── Interface rapide (debug / tests) ──────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json

    logging.basicConfig(level=logging.DEBUG)

    if len(sys.argv) < 2:
        print("Usage : python email_parser.py <chemin_vers_email.eml|.msg>")
        sys.exit(1)

    res = extract_email(sys.argv[1])

    summary = {
        "file":          res.file_name,
        "type":          res.file_type,
        "subject":       res.metadata.subject,
        "sender":        res.metadata.sender.email_address if res.metadata.sender else None,
        "date":          res.metadata.date_str,
        "organisme":     res.metadata.organisme_detected,
        "direction":     res.metadata.direction.value,
        "is_reply":      res.metadata.is_reply,
        "reply_chain":   len(res.reply_chain),
        "attachments":   [
            {
                "name": a.filename,
                "extension": a.extension,
                "rd_relevant": a.is_rd_relevant,
                "has_content": bool(a.content),
                "size_bytes": a.size_bytes,
                "content_type": a.content_type,
            }
            for a in res.attachments
        ],
        "rd_sections":   res.detected_rd_sections,
        "tags":          res.tags,
        "confidence":    res.confidence_score,
        "errors":        res.extraction_errors,
        "chunks_preview": [c[:300] + "…" for c in res.text_chunks[:3]],
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))