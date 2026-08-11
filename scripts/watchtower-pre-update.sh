#!/bin/sh
# Watchtower pre-update lifecycle hook.
#
# Defers container replacement while the app is mid-operation by exiting 75
# (EX_TEMPFAIL).  Watchtower skips this container for the current poll cycle
# and retries on the next one (~300 s later under the default configuration).
#
# Exit codes (nickfedor/watchtower fork v1.20.0):
#   0  -> proceed with update
#   75 -> skip this container this cycle (EX_TEMPFAIL)
#   any other -> aborts watchtower's entire update run
#
# This script MUST exit with only 0 or 75.  Every error path -- connection
# refused, timeout, missing python, malformed JSON -- falls through to exit 0
# so an unhealthy or restarting app never blocks its own replacement.

URL="http://localhost:8000/health/busy"
TIMEOUT=5

busy=$(python -c "
import sys
try:
    import json, urllib.request
    with urllib.request.urlopen('$URL', timeout=$TIMEOUT) as r:
        data = json.loads(r.read())
    sys.stdout.write('true' if data.get('busy') is True else 'false')
except Exception:
    sys.stdout.write('false')
" 2>/dev/null) || busy=false

if [ "$busy" = "true" ]; then
    exit 75
fi
exit 0
