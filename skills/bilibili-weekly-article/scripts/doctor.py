"""Report local Bilibili prerequisites without logging in or accessing the network."""
import importlib.util
import json
import sys


def main():
    required = ('PIL', 'requests', 'qrcode', 'bilibili_api', 'httpx')
    dependencies = {name: importlib.util.find_spec(name) is not None for name in required}
    ready = sys.version_info >= (3, 11) and all(dependencies.values())
    print(json.dumps({
        'status': 'prerequisites_found' if ready else 'setup_required',
        'python': sys.version.split()[0], 'python_supported': sys.version_info >= (3, 11),
        'platform': sys.platform, 'dependencies': dependencies,
        'browser_required': False, 'network_accessed': False, 'account_verified': False,
    }, ensure_ascii=False, indent=2))
    return 0 if ready else 1


if __name__ == '__main__': raise SystemExit(main())
