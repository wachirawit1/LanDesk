import hashlib
from pathlib import Path
import shutil
import zipfile

root = Path.cwd()
output = root / 'outputs/v0.2.0'
source = root / 'LanDesk'
binary = output / 'Windows/LanDesk'
for name in ['README.md', 'TEST-REPORT.md', 'RELEASE-NOTES-v0.2.0.md']:
    shutil.copy2(source / name, binary / name)
shutil.copy2(root / 'SECURITY.md', binary / 'SECURITY.md')
shutil.copytree(root / 'Windows/LanDesk/licenses', binary / 'licenses', dirs_exist_ok=True)
shutil.copy2(source / 'RELEASE-NOTES-v0.2.0.md', output / 'RELEASE-NOTES-v0.2.0.md')
checksums = []
for folder, filename in [(source, 'LanDesk-v0.2.0-source.zip'), (binary, 'LanDesk-v0.2.0-Windows-x64.zip')]:
    destination = output / filename
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts:
                continue
            assert path.suffix.lower() not in {'.pem', '.key', '.dat', '.partial'}, path
            archive.write(path, str(Path('LanDesk') / path.relative_to(folder)))
        if folder == source:
            archive.write(root / 'SECURITY.md', 'LanDesk/SECURITY.md')
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert not any('smoke-profile' in name or 'ui-test-profile' in name or 'settings-v2' in name for name in archive.namelist())
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    checksums.append(f'{digest}  {filename}')
    print(f'{filename}: {destination.stat().st_size / 1024 / 1024:.1f} MB; ZIP verified')
(output / 'SHA256SUMS.txt').write_text('\n'.join(checksums) + '\n', encoding='utf-8')
print('\n'.join(checksums))
