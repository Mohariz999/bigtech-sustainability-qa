"""Small local request and run provenance helpers. No external monitoring."""
import hashlib
import json
import subprocess
import time
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def request_record(mode):
    return {'request_id': uuid.uuid4().hex, 'retrieval_mode': mode}


def end_to_end(start):
    return round(time.perf_counter() - start, 4)


def hash_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_manifest(root, dataset, config, *, source_log=None):
    root = Path(root)
    def git(*args):
        try:
            return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL).decode().strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    revision = git('rev-parse', 'HEAD')
    # Hash actual Python files as well as revision: local/uncommitted code matters.
    corpus = root / 'data' / 'corpus.sqlite'
    corpus_clean = corpus.exists() and not Path(str(corpus) + '-wal').exists()
    hashes = {str(p.relative_to(root)): hash_file(p) for p in sorted(root.rglob('*.py'))
              if '.git' not in p.parts and '.venv' not in p.parts}
    return {'schema_version': 1, 'run_id': uuid.uuid4().hex,
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'code_revision': revision, 'working_tree_status': git('status', '--porcelain'),
            'code_file_sha256': hashes, 'dataset_sha256': hash_file(dataset),
            'python_version': sys.version,
            'requirements_sha256': hash_file(root / 'requirements.txt') if (root / 'requirements.txt').exists() else None,
            'corpus_sha256': hash_file(corpus) if corpus_clean else None,
            'corpus_identity_status': 'database_file_hash_not_snapshot_lock' if corpus_clean else 'absent_or_active_wal',
            'config': config, 'source_log_sha256': hash_file(source_log) if source_log else None,
            'result_kind': config.get('result_kind', 'rescore_saved_outputs' if source_log else 'live_generation'),
            'pricing': 'repository PRICES snapshot; actual response model used, unknown model rates explicitly marked as estimates'}


def create_run(root, dataset, config, *, source_log=None):
    manifest = run_manifest(root, dataset, config, source_log=source_log)
    path = Path(root) / 'logs' / 'evaluation-runs' / manifest['run_id']
    path.mkdir(parents=True, exist_ok=False)
    (path / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return path
