#!/bin/bash
# Bygger "Auracast Sender.app" från källkoden (kräver: pip3 install pyinstaller)
# Inkluderar mikrofonbehörighets-nyckeln i Info.plist – utan den blockerar
# macOS mikrofonen TYST i paketerade appar.
set -e
cd "$(dirname "${BASH_SOURCE[0]}")"

rm -rf build dist "Auracast Sender.spec"

python3 -m PyInstaller \
  --windowed \
  --name "Auracast Sender" \
  --osx-bundle-identifier "se.guldbrand.auracast-sender" \
  --hidden-import uvicorn \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.lifespan.on \
  --hidden-import multipart \
  --noconfirm \
  main.py

APP="dist/Auracast Sender.app"

# KRITISKT: mikrofonbeskrivning (annars tyst mic-blockering av TCC)
/usr/libexec/PlistBuddy -c \
  "Add :NSMicrophoneUsageDescription string 'Auracast Sender behöver mikrofonen för att sända ljud till dina hörapparater.'" \
  "$APP/Contents/Info.plist"

# Rensa attribut + ad-hoc-signera
xattr -cr "$APP"
codesign --force --deep -s - "$APP"

echo ""
echo "Klar: $APP"
