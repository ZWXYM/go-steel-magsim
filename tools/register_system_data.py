"""Register a private, verified data-workpack copy in the chosen task storage."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.system_data import WorkpackStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage', required=True, help='The same task storage shown on /system')
    parser.add_argument('--file', required=True, help='Existing system_data_workpack_v2/v3 ZIP; kept unchanged')
    parser.add_argument('--title', required=True)
    parser.add_argument('--description', default='')
    args = parser.parse_args()
    record = WorkpackStore(Path(args.storage) / 'system_data').register(args.file, args.title, args.description)
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
