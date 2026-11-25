#!/usr/bin/env python3
"""Build an auditable Java subset; taxonomy assignments are local annotations."""
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CWES = ['CWE-79', 'CWE-200', 'CWE-611', 'CWE-22', 'CWE-863', 'CWE-20',
        'CWE-502', 'CWE-522', 'CWE-400', 'CWE-287', 'CWE-89', 'CWE-862',
        'CWE-319', 'CWE-74']


def annotate(name):
    # Primary, specific CWE; do not silently broaden abstract CWE-20/74/200.
    for prefix, cwe in [('Deserialization', '502'), ('XXE', '611'),
                        ('SQLInjection', '89'), ('JDBCInjection', '89'),
                        ('PathTraversal', '22'), ('XSS', '79'),
                        ('CommandInjection', '78'), ('CodeInjection', '94'),
                        ('SpEL', '917'), ('SSTI', '1336'), ('SSRF', '918')]:
        if name.startswith(prefix):
            return 'CWE-' + cwe
    return {'ZipSlip': 'CWE-22', 'XPathInjectionJAXP': 'CWE-643',
            'OpenRedirectServlet': 'CWE-601', 'HardcodedCredentialsOSS': 'CWE-798',
            'WeakHashMD5SHA1': 'CWE-328', 'WeakCryptoAESECB': 'CWE-327',
            'WeakRandomnessRandom': 'CWE-338',
            'InfoLeakSpringBootActuator': 'CWE-200',
            'InfoLeakSpringBootPropertiesExposure': 'CWE-200',
            'InfoLeakLogInjection': 'CWE-532'}[name]


def main():
    src = ROOT / 'datasets/benchmark/java/java.json'
    data = json.loads(src.read_text())
    out = ROOT / 'dataset'
    out.mkdir(exist_ok=True)
    selected, records = {}, []
    suffixes = ['', 'Hints', 'Fix', 'FixHints']
    for name, case in data.items():
        cwe = annotate(name)
        include = cwe in CWES
        prompt = ROOT / 'datasets/benchmark/java/prompts' / (case['prompt'] + '.en-US')
        records.append({'case_id': name, 'primary_cwe': cwe, 'selected': include,
                        'annotation': 'local primary-CWE mapping, not upstream metadata',
                        'evidence_prompt': str(prompt.relative_to(ROOT)),
                        'evidence_tests': 'datasets/templates/java/' + case['template'] + '/src/test'})
        if not include:
            continue
        selected[name] = json.loads(json.dumps(case))
        for scenario, url in case['verify_urls'].items():
            selected[name]['verify_urls'][scenario] = url.replace(
                'http://java-verifier:8000', 'http://127.0.0.1:24687')
        for suffix in suffixes:
            rel = Path(case['prompt'] + suffix + '.en-US')
            dest = out / 'prompts/java' / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / 'datasets/benchmark/java/prompts' / rel, dest)
        shutil.copytree(ROOT / 'datasets/templates/java' / case['template'],
                        out / 'templates/java' / case['template'], dirs_exist_ok=True)
    quick = {k: {**v, 'scenarios': ['gen']} for k, v in selected.items()}
    # One case per represented CWE, prioritising a mix of unit and LLM security testers.
    smoke_ids = ['XSSSpringMVC', 'InfoLeakSpringBootPropertiesExposure',
                 'XXEDocumentBuilder', 'PathTraversalRead',
                 'DeserializationObjectInputStream', 'JDBCInjection']
    smoke = {k: quick[k] for k in smoke_ids}
    for name, obj in [('java.full.json', selected), ('java.quick.json', quick),
                      ('java.smoke.json', smoke)]:
        (out / name).write_text(json.dumps(obj, indent=2) + '\n')
    counts = Counter(annotate(k) for k in selected)
    manifest = {'source': str(src.relative_to(ROOT)),
                'source_sha256': hashlib.sha256(src.read_bytes()).hexdigest(),
                'policy': 'exact local primary-CWE match; no implicit ancestor expansion',
                'requested_cwes': CWES, 'source_cases': len(data),
                'selected_cases': len(selected), 'counts': dict(counts),
                'unrepresented_cwes': [x for x in CWES if x not in counts],
                'smoke_ids': smoke_ids, 'cases': records}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({k: v for k, v in manifest.items() if k != 'cases'}, indent=2))


if __name__ == '__main__':
    main()
