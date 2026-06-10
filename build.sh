#!/bin/zsh
# Build Paper Reader.app and PaperReader.dmg
set -e
cd "$(dirname "$0")"

echo "==> Running tests"
.venv/bin/pytest backend/tests -q

echo "==> Building Paper Reader.app"
rm -rf build dist
.venv/bin/pyinstaller --noconfirm --windowed \
  --name "Paper Reader" \
  --icon assets/icon.icns \
  --add-data "frontend:frontend" \
  --osx-bundle-identifier "in.karthikeyan.paperreader" \
  desktop.py

echo "==> Packaging PaperReader.dmg"
rm -rf dist/dmgroot PaperReader.dmg
mkdir dist/dmgroot
cp -R "dist/Paper Reader.app" dist/dmgroot/
ln -s /Applications dist/dmgroot/Applications
cat > "dist/dmgroot/READ ME FIRST.txt" <<'EOF'
Paper Reader — listen to academic papers with read-along highlighting.

INSTALL
1. Drag "Paper Reader" onto the Applications folder.
2. FIRST LAUNCH ONLY: right-click (or Control-click) Paper Reader
   in Applications and choose "Open", then click "Open" in the dialog.
   (This is needed because the app isn't registered with Apple —
   a normal double-click works from then on.)

BETTER VOICES (optional, recommended)
System Settings -> Accessibility -> Spoken Content -> System Voice
-> Manage Voices... and download a "Premium" voice such as Ava.
EOF
hdiutil create -volname "Paper Reader" -srcfolder dist/dmgroot -ov -format UDZO PaperReader.dmg

echo "==> Done: $(du -h PaperReader.dmg | cut -f1) PaperReader.dmg"
