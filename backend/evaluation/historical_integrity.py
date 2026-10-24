"""Opt-in historical text validation; existing replay utilities do not use this yet.

Trust resides in the reviewed manifest/code, not in claims inside an artifact.
Git revisions document review provenance; no Git or network access occurs here.
Only CRLF representation is ignored. Binary models and current production source
are never historical artifacts. This is not protection against manifest/code edits.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / 'historical_integrity_manifest.json'


class HistoricalIntegrityError(ValueError):
    pass


class LegacyFingerprintUnavailable(HistoricalIntegrityError):
    pass


def _manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))


def _validated_entry(artifact, root, manifest):
    # Exact relative POSIX names only: no aliasing, traversal or unknown files.
    if not isinstance(artifact, str) or artifact not in manifest['artifacts']:
        raise HistoricalIntegrityError('Unknown historical artifact')
    base = Path(root).resolve()
    path = (base / artifact).resolve()
    if not path.is_relative_to(base):
        raise HistoricalIntegrityError('Historical artifact escapes evidence root')
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise HistoricalIntegrityError('Historical artifact missing or unreadable: ' + artifact) from error
    normalized = raw.replace(b'\r\n', b'\n')
    if b'\r' in normalized:
        raise HistoricalIntegrityError('Lone CR in historical text: ' + artifact)
    entry = manifest['artifacts'][artifact]
    digest = hashlib.sha256(normalized).hexdigest()
    if digest != entry['content_sha256_lf']:
        raise HistoricalIntegrityError('Historical content changed: ' + artifact)
    return entry, digest


def validate_historical_content(artifact, *, root=ROOT):
    """Return the pinned normalized-content digest after validation; never write."""
    return _validated_entry(artifact, root, _manifest())[1]


def recorded_legacy_fingerprint(artifact, *, root=ROOT):
    """Return recorded provenance only after content validation, never synthesize."""
    entry, _ = _validated_entry(artifact, root, _manifest())
    if entry['legacy_sha256'] is None:
        raise LegacyFingerprintUnavailable('No recorded legacy fingerprint: ' + artifact)
    return entry['legacy_sha256']


def recorded_dataset_fingerprint(dataset='original', *, root=ROOT):
    """Validate every member in the pinned concatenation order, then return provenance.

    This does not re-create historical file bytes or hash current concatenations.
    The aggregate was independently checked against its recorded historical source.
    """
    manifest = _manifest()
    if not isinstance(dataset, str) or dataset not in manifest['datasets']:
        raise HistoricalIntegrityError('Unknown historical dataset')
    entry = manifest['datasets'][dataset]
    # This aggregate is defined over exactly these three ordered artifacts.
    # Reject malformed membership before validating files or returning provenance.
    expected = ['corpus.json', 'cases.json', 'fixture_rankings.json']
    if dataset != 'original' or entry.get('members') != expected:
        raise HistoricalIntegrityError('Invalid historical dataset membership or ordering')
    for artifact in entry['members']:
        _validated_entry(artifact, root, manifest)
    return entry['legacy_sha256']


def current_file_byte_fingerprint(path):
    """Exact bytes of any current file, including binaries. No historical assertion."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
