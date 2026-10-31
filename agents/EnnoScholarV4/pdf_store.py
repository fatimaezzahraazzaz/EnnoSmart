"""Content-addressed PDFs: bounded staging, checked bytes and atomic publication."""
import hashlib
import os
import re
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_lock = threading.RLock()


@contextmanager
def storage_guard(root):
    """Coordinate the same directory across both threads and backend workers."""
    with _lock:
        with (root/'.storage.lock').open('a+b') as stream:
            if not stream.tell():
                stream.write(b'0'); stream.flush()
            until = time.monotonic()+5
            while True:
                try:
                    stream.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= until:
                        raise TimeoutError('PDFStorageLockTimeout')
                    time.sleep(.02)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def directory():
    root = Path(os.getenv("ENNOSCHOLAR_V4_PDF_DIRECTORY", str(Path(__file__).resolve().parents[2]/"backend_api"/"storage"/"scholar_v4"/"pdf")))
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            digest.update(block)
    return digest.hexdigest()


class StagedDocument:
    """Owns only this download's temporary file and reservation, never user files."""
    def __init__(self, config):
        self.config, self.path, self.size, self.sha256 = config, None, 0, ''
        if not (0 < config.max_pdf_disk_bytes <= 536870912 and
                config.max_pdf_disk_bytes <= config.max_pdf_storage_bytes <= 21474836480 and config.min_pdf_free_bytes >= 0):
            raise ValueError('PDFStorageLimitsInvalid')
        root = directory()
        with storage_guard(root):
            reservations = {p.with_suffix('.part'): int(p.read_text(encoding='ascii')) for p in root.glob('.download-*.reservation')}
            used = sum(p.stat().st_size for p in root.iterdir() if p.is_file() and p not in reservations
                and not p.name.endswith('.reservation') and p.name != '.storage.lock')
            reserved = sum(reservations.values())
            if used+reserved+config.max_pdf_disk_bytes > config.max_pdf_storage_bytes:
                raise ValueError('PDFStorageQuotaExceeded')
            if shutil.disk_usage(root).free-reserved-config.max_pdf_disk_bytes < config.min_pdf_free_bytes:
                raise ValueError('PDFStorageFreeSpaceInsufficient')
            fd, name = tempfile.mkstemp(prefix='.download-', suffix='.part', dir=root)
            os.close(fd)
            self.path = Path(name)
            self.path.with_suffix('.reservation').write_text(str(config.max_pdf_disk_bytes), encoding='ascii')

    def close(self):
        if self.path is not None:
            with storage_guard(self.path.parent):
                try:
                    self.path.unlink(missing_ok=True)
                except PermissionError:
                    # A native parser can retain a handle after a parse failure
                    # on Windows. An inert .part is counted by the quota and is
                    # never treated as an archived document.
                    pass
                self.path.with_suffix('.reservation').unlink(missing_ok=True)
                self.path = None


def archive_file(staged):
    """Hash, parse, then atomically publish on the same filesystem.
    Existing corrupt files are reported, never silently deleted or overwritten.
    """
    import fitz
    path = staged.path
    size = path.stat().st_size
    if not 0 < size <= staged.config.max_pdf_disk_bytes:
        raise ValueError('PDFDiskSizeInvalid')
    with path.open('rb') as stream:
        if stream.read(4) != b'%PDF':
            raise ValueError('PDF requis.')
    digest = file_digest(path)
    if (staged.sha256 and digest != staged.sha256) or (staged.size and size != staged.size):
        raise ValueError('PDFIntegrityMismatch')
    with fitz.open(str(path), filetype='pdf') as document:
        if document.needs_pass or not len(document):
            raise ValueError('PDF protégé ou vide.')
    filename = digest+'.pdf'
    target = path.parent/filename
    with storage_guard(path.parent):
        if target.exists():
            if target.stat().st_size != size or file_digest(target) != digest:
                raise ValueError('ExistingPDFIntegrityMismatch')
        else:
            os.replace(path, target)
    staged.close()
    return filename


def save_pdf(data, *, max_bytes=None):
    from dataclasses import replace
    from .config import ScholarV4Config
    config = ScholarV4Config.from_env()
    limit = config.max_pdf_bytes if max_bytes is None else max_bytes
    if limit <= 0 or not data.startswith(b'%PDF') or len(data) > limit:
        raise ValueError(f'PDF requis, taille maximale {limit} octets.')
    staged = StagedDocument(replace(config, max_pdf_disk_bytes=limit))
    try:
        with staged.path.open('wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        staged.size, staged.sha256 = len(data), hashlib.sha256(data).hexdigest()
        return archive_file(staged)
    finally:
        staged.close()


def pdf_path(filename):
    if not re.fullmatch(r'[a-f0-9]{64}\.pdf', filename):
        raise ValueError('Identifiant PDF invalide.')
    path = directory()/filename
    if file_digest(path) != filename[:-4]:
        raise ValueError('ExistingPDFIntegrityMismatch')
    return path


def extract_pages(filename, page_start, page_limit, *, config=None, page_offsets=None, deadline=None, check=lambda: None):
    from .config import ScholarV4Config
    from .tools.extraction import pdf_chunks
    config = config or ScholarV4Config.from_env()
    progress = {}
    chunks, truncated = pdf_chunks(pdf_path(filename), config, page_start, page_limit,
        progress=progress, page_offsets=page_offsets, deadline=deadline, check=check)
    return [c.model_dump() for c in chunks], progress['page_count'], truncated, progress
