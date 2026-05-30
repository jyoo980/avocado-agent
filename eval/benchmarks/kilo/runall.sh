#!/usr/bin/env bash
# Re-verify every kilo.c function that carries a CBMC contract, and (re)generate
# kilo-log.jsonl.  Fields are tab-separated: function, harness, STUBS, GI, CB.
# A field of "-" means empty.
set -u
cd "$(dirname "$0")"

rows() {
cat <<'TSV'
is_separator	h_is_separator	-	-	-
editorSyntaxToColor	h_editorSyntaxToColor	-	-	-
editorFileWasModified	h_editorFileWasModified	-	-	-
editorFreeRow	h_editorFreeRow	-	-	-
editorRowHasOpenComment	h_editorRowHasOpenComment	-	-	-
abAppend	h_abAppend	-	-	-
abFree	h_abFree	-	-	-
editorUpdateSyntax	h_editorUpdateSyntax	-	-	-
editorUpdateRow	h_editorUpdateRow	-	--replace-call-with-contract editorUpdateSyntax	--unwind 12 --unwinding-assertions
editorRowDelChar	h_editorRowDelChar	-	--replace-call-with-contract editorUpdateRow	--unwind 12 --unwinding-assertions
editorRowInsertChar	h_editorRowInsertChar	-	--replace-call-with-contract editorUpdateRow	--unwind 12 --unwinding-assertions
editorRowAppendString	h_editorRowAppendString	-	--replace-call-with-contract editorUpdateRow	--unwind 12 --unwinding-assertions
editorRowsToString	h_editorRowsToString	-	-	--unwind 4 --unwinding-assertions
editorDelRow	h_editorDelRow	-	--replace-call-with-contract editorFreeRow	--unwind 4 --unwinding-assertions
disableRawMode	h_disableRawMode	tcsetattr	-	-
editorAtExit	h_editorAtExit	tcsetattr	--replace-call-with-contract disableRawMode	-
enableRawMode	h_enableRawMode	tcsetattr|tcgetattr|isatty|atexit	-	-
getCursorPosition	h_getCursorPosition	read|write|sscanf|__isoc99_sscanf|vsscanf|__isoc99_vsscanf	-	--unwind 33
getWindowSize	h_getWindowSize	ioctl|write|snprintf|strlen	--replace-call-with-contract getCursorPosition	--unwind 33
TSV
}

LOG=kilo-log.jsonl
: > "$LOG"
fail=0
while IFS=$'\t' read -r F H S G C; do
  [ -z "$F" ] && continue
  [ "$S" = "-" ] && S=""
  [ "$G" = "-" ] && G=""
  [ "$C" = "-" ] && C=""
  out=$(STUBS="$S" GI="$G" CB="$C" ./verify.sh "$F" "$H" 2>&1 | tail -3)
  verdict=$(grep -o "VERIFICATION SUCCESSFUL\|VERIFICATION FAILED" <<<"$out" | tail -1)
  printf '%-26s %s\n' "$F" "${verdict:-NO VERDICT}"
  [ "$verdict" = "VERIFICATION SUCCESSFUL" ] || fail=1
  cmd="STUBS='$S' GI='$G' CB='$C' ./verify.sh $F $H"
  printf '{ "file": "kilo.c", "function": "%s", "command": "%s" }\n' "$F" "$cmd" >> "$LOG"
done < <(rows)
echo "----"
[ $fail -eq 0 ] && echo "ALL VERIFIED" || echo "SOME FAILED"
