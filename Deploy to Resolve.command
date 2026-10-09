#!/bin/bash
# Double-click this in Finder to build buddy.zip and copy it (with Buddy.py)
# into DaVinci Resolve's Scripts > Utility folder. Safe to run again any time
# you've changed the code - it just overwrites the old copy.
set -e
cd "$(dirname "$0")"

echo "Installing/updating Buddy's Python dependencies..."
python3 -m pip install --user -r installer/requirements.txt

echo
echo "Building buddy.zip and copying it into Resolve's Scripts > Utility folder..."
python3 build_buddy_zip.py --deploy

echo
echo "Done. Fully quit DaVinci Resolve (Cmd+Q) if it's open, then reopen it and"
echo "look under Workspace > Scripts > Buddy."
echo
read -p "Press Return to close this window..."
