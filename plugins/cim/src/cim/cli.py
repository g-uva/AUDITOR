"""Thin CLI over the same offline library functions and explicit transport."""
import argparse
import json
from pathlib import Path
import sys
from .semantic import CIMError, import_batch, require, validate_batch
from .representations import from_representation, to_representation
from .targets import TARGET, export_draft, send_batch


def read(path):
    return json.loads(Path(path).read_text())


def main(argv=None):
    parser = argparse.ArgumentParser(prog='cim')
    parser.add_argument('command', choices=('validate', 'convert', 'send'))
    parser.add_argument('input')
    parser.add_argument('--input-format', choices=('auditor', 'canonical', 'saref', 'schemaorg'), default='canonical')
    parser.add_argument('--mapping')
    parser.add_argument('--context')
    parser.add_argument('--output-format', choices=('canonical', 'saref', 'schemaorg', TARGET), default='canonical')
    parser.add_argument('--target', default=TARGET)
    parser.add_argument('--output')
    parser.add_argument('--sidecar', help='required local provenance output for draft conversion/send')
    parser.add_argument('--pear-url')
    parser.add_argument('--timeout', type=float, default=10)
    args = parser.parse_args(argv)
    try:
        document = read(args.input)
        ignored = []
        if args.input_format == 'auditor':
            require(args.mapping and args.context, 'AUDITOR input requires --mapping and --context')
            result = import_batch(document if isinstance(document, list) else [document], read(args.mapping), read(args.context))
            observations, ignored = result['observations'], result['ignored_fields']
        elif args.input_format == 'canonical':
            observations = document['observations'] if isinstance(document, dict) and 'observations' in document else (document if isinstance(document, list) else [document])
            if isinstance(document, dict):
                ignored = document.get('ignored_fields', [])
        else:
            observations = [from_representation(item, args.input_format) for item in (document if isinstance(document, list) else [document])]
        validate_batch(observations)
        for field in ignored:
            print('ignored: ' + json.dumps(field), file=sys.stderr)
        result = {'observations': observations, 'ignored_fields': ignored}
        if args.command == 'send' or (args.command == 'convert' and args.output_format == TARGET):
            require(args.sidecar, 'draft output/send requires --sidecar to retain local provenance')
            require(not args.output or Path(args.sidecar).resolve() != Path(args.output).resolve(), 'sidecar and output must be separate files')
            require(Path(args.sidecar).resolve() != Path(args.input).resolve(), 'sidecar must not overwrite input')
            draft = export_draft(observations, args.target)
            Path(args.sidecar).write_text(json.dumps(draft['sidecar'], indent=2, allow_nan=False) + '\n')
            if args.command == 'send':
                require(args.pear_url, 'send requires --pear-url')
                require(args.timeout > 0, 'timeout must be positive')
                result = send_batch(observations, args.pear_url, target=args.target, timeout=args.timeout)
            else:
                result = {'target': draft['target'], 'records': draft['records']}
        elif args.command == 'convert' and args.output_format in ('saref', 'schemaorg'):
            result = [to_representation(o, args.output_format) for o in observations]
        elif args.command == 'validate':
            result = {'valid': True, 'observations': len(observations), 'ignored_fields': ignored}
        output = json.dumps(result, indent=2, allow_nan=False) + '\n'
        if args.output:
            Path(args.output).write_text(output)
        else:
            print(output, end='')
        return 0
    except (ValueError, TypeError, KeyError, OSError, OverflowError) as exc:
        print(f'cim: {exc}', file=sys.stderr)
        return 1
