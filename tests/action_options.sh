#!/usr/bin/env sh

# Tests for final action options with various snipsnap commands
# Arguments:
# 1. path to tested snipsnap executable

# Dependencies:
# - display command (imagemagick)

# HOW TO USE:
# - Start the script with path to tested snipsnap executable as the first
#   argument
#
# - Read messages from stdout and see if snipsnap sends the right notifications
#
#   Some commands will pin screenshots to the screen. Check if that is happening
#   correctly. NOTE: the screen command will pin one screenshot over your entire
#   screen, so don't be confused by that.
#
# - When the snipsnap gui is tested, follow the instructions from the system
#   notifications
#
# - Some tests may ask you for confirmation in the CLI before continuing.
# - Whenever the --raw option is tested, the `display` command is used to open
#   the image from stdout in a window. Just close that window.
#

SNIPSNAP="$1"
[ -z "$SNIPSNAP" ] && SNIPSNAP="snipsnap"

# --raw >/dev/null is a hack that makes the subcommand wait for the daemon to
# finish the pending action
snipsnap() {
    command "$SNIPSNAP" "$@" --raw >/tmp/img.png
}

# Print the given command and run it
cmd() {
    echo "$*" >&2
    "$@"
    sleep 1
}

notify() {
  if [ "$SNIPSNAP_PLATFORM" = "MAC" ]
  then
osascript -  "$1"  <<EOF
  on run argv
    display notification (item 1 of argv) with title "SnipSnap"
  end run
EOF

  else
    notify-send "GUI Test 1: --path" "Make a selection, then accept"
  fi
}

display_img() {
  if [ "$SNIPSNAP_PLATFORM" = "MAC" ]
  then
    open -a Preview.app -f
  else
    display
  fi
}




wait_for_key() {
    echo "Press Enter to continue..." >&2 && read ____
}

# NOTE: Upload option is intentionally not tested

#   snipsnap full & screen
# ┗━━━━━━━━━━━━━━━━━━━━━━━━┛

for subcommand in full screen
do
    cmd snipsnap "$subcommand" --path /tmp/
    cmd snipsnap "$subcommand" --clipboard
    cmd command "$SNIPSNAP" "$subcommand" --raw | display_img
    [ "$subcommand" = "full" ] && sleep 1
    echo
done

echo "The next command will pin a screenshot over your entire screen."
echo "Make sure to close it afterwards"
echo "Press Enter to continue..."
read ____
snipsnap screen --pin
sleep 1

#   snipsnap gui
# ┗━━━━━━━━━━━━━━━┛

wait_for_key
notify "GUI Test 1: --path" #"Make a selection, then accept"
cmd snipsnap gui --path /tmp/
wait_for_key
notify "GUI Test 2: Clipboard" "Make a selection, then accept"
cmd snipsnap gui --clipboard
wait_for_key
notify "GUI Test 3: Print geometry" "Make a selection, then accept"
cmd command "$SNIPSNAP" gui --print-geometry
wait_for_key
notify "GUI Test 4: Pin" "Make a selection, then accept"
cmd snipsnap gui --pin
wait_for_key
notify "GUI Test 5: Print raw" "Make a selection, then accept"
cmd command "$SNIPSNAP" gui --raw | display_img
wait_for_key
notify "GUI Test 6: Copy on select" "Make a selection, snipsnap will close automatically"
cmd snipsnap gui --clipboard --accept-on-select
wait_for_key
notify "GUI Test 7: File dialog on select" "After selecting, a file dialog will open"
cmd snipsnap gui --accept-on-select

# All options except for --print-geometry (incompatible with --raw)
wait_for_key
notify "GUI Test 8: All actions except print-geometry" "Just make a selection"
cmd command "$SNIPSNAP" gui -p /tmp/ -c -r --pin | display_img

echo '>> All tests done.'
