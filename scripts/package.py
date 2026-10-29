"""Create public release artifacts without a developer provisioning profile."""
import hashlib
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

def archive_tree(archive, root, prefix):
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        if path.name == 'embedded.mobileprovision' or '_CodeSignature' in path.parts:
            raise SystemExit('Refusing to publish a signed/personal app bundle')
        archive.write(path, str(pathlib.PurePosixPath(prefix) / path.relative_to(root)))

mode = sys.argv[1]
if mode in {'device', 'simulator'}:
    app, dist = pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
    dist.mkdir(parents=True, exist_ok=True)
    filename = 'DarkkitaHealth-unsigned.ipa' if mode == 'device' else 'DarkkitaHealth-simulator.zip'
    prefix = 'Payload/DarkkitaHealth.app' if mode == 'device' else 'DarkkitaHealth.app'
    with zipfile.ZipFile(dist / filename, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive_tree(archive, app, prefix)
elif mode == 'plugin':
    dist = pathlib.Path(sys.argv[2]); dist.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dist / 'astrbot_plugin_apple_health.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in ['main.py', '__init__.py', 'metadata.yaml', '_conf_schema.json', 'LICENSE']:
            archive.write(ROOT / name, name)
        for name in ['views', '.astrbot-plugin']:
            archive_tree(archive, ROOT / name, name)
else:
    raise SystemExit('mode: device | simulator | plugin')
