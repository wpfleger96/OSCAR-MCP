# SNORE CLI Reference

Auto-generated from `--help`. Do not edit manually.

This is the complete CLI reference for SNORE. For quick start examples and usage guides, see [README.md](../README.md).

## `snore`

```
Usage: snore [OPTIONS] COMMAND [ARGS]...

  SNORE: CPAP Data Management Tool

Options:
  --version      Show version and check for updates
  -v, --verbose  Enable verbose logging
  --help         Show this message and exit.

Commands:
  analysis     Analyze CPAP sessions and view results.
  completions  Manage shell tab completion.
  config       Configuration management commands.
  db           Database management commands.
  event        Event data export commands.
  import       Import CPAP data from device SD card or directory.
  logs         Log file management commands.
  profile      Profile management commands.
  session      Session management commands.
  setup        Install SNORE globally as a uv tool.
  stats        Show therapy usage and clinical statistics.
  upgrade      Upgrade SNORE to the latest version.
  validate     Run batch validation across multiple sessions.
  waveform     Waveform inspection and visualization commands.
```

## `snore analysis`

```
Usage: snore analysis [OPTIONS] COMMAND [ARGS]...

  Analyze CPAP sessions and view results.

Options:
  --help  Show this message and exit.

Commands:
  delete  Delete analysis results without deleting the sessions themselves.
  list    List sessions with analysis status.
  run     Run analysis on CPAP sessions.
  show    Display stored analysis results.
```

## `snore analysis delete`

```
Usage: snore analysis delete [OPTIONS]

  Delete analysis results without deleting the sessions themselves.

Options:
  --session-id TEXT  Comma-separated session IDs to delete analysis for (e.g.,
                     '1,2,3')
  --from [%Y-%m-%d]  Delete analysis for sessions from this date (YYYY-MM-DD)
  --to [%Y-%m-%d]    Delete analysis for sessions up to this date (YYYY-MM-DD)
  --all              Delete all analysis results
  --all-versions     Delete all analysis versions (default: only latest)
  --dry-run          Preview what would be deleted without deleting
  -f, --force        Skip confirmation prompt
  --db PATH          Database path
  --help             Show this message and exit.
```

## `snore analysis list`

```
Usage: snore analysis list [OPTIONS]

  List sessions with analysis status.

Options:
  --profile TEXT                  Profile username (optional if default set)
  --all-profiles                  Include all profiles (ignores --profile)
  --from [%Y-%m-%d]               Start date for filtering (YYYY-MM-DD)
  --to [%Y-%m-%d]                 End date for filtering (YYYY-MM-DD)
  --limit INTEGER                 Max sessions to show (use 0 for all)
  --analyzed-only                 Show only analyzed sessions
  --sort-by [date-asc|date-desc|profile|session-id]
                                  Sort order for results (default: date-desc)
  --db PATH                       Database path
  --help                          Show this message and exit.
```

## `snore analysis run`

```
Usage: snore analysis run [OPTIONS]

  Run analysis on CPAP sessions.

Options:
  --profile TEXT        Profile username (optional if default set)
  --session-id INTEGER  Analyze single session by ID
  --date [%Y-%m-%d]     Analyze single session by date (YYYY-MM-DD)
  --from [%Y-%m-%d]     Start date for batch analysis (YYYY-MM-DD)
  --to [%Y-%m-%d]       End date for batch analysis (YYYY-MM-DD)
  --db PATH             Database path
  --no-store            Don't store results in database
  --debug-events        Print detailed comparison of machine vs programmatic
                        event detection
  -m, --mode TEXT       Detection mode(s) to run. Default: aasm. Can specify
                        multiple: -m aasm -m resmed
  --all-modes           Run all available detection modes
  --plain               Plain output without colors/borders
  --help                Show this message and exit.
```

## `snore analysis show`

```
Usage: snore analysis show [OPTIONS]

  Display stored analysis results.

Options:
  --profile TEXT        Profile username (optional if default set)
  --session-id INTEGER  Show analysis for session ID
  --date [%Y-%m-%d]     Show analysis for session on date (YYYY-MM-DD)
  --db PATH             Database path
  --plain               Plain output without colors/borders
  --help                Show this message and exit.
```

## `snore completions`

```
Usage: snore completions [OPTIONS] COMMAND [ARGS]...

  Manage shell tab completion.

Options:
  --help  Show this message and exit.

Commands:
  bash       Output bash completion script for manual installation.
  install    Install shell completion to config file.
  uninstall  Remove shell completion from config file.
  zsh        Output zsh completion script for manual installation.
```

## `snore completions bash`

```
Usage: snore completions bash [OPTIONS]

  Output bash completion script for manual installation.

Options:
  --help  Show this message and exit.
```

## `snore completions install`

```
Usage: snore completions install [OPTIONS]

  Install shell completion to config file.

Options:
  --shell [bash|zsh]  Shell type (auto-detected if not specified)
  --help              Show this message and exit.
```

## `snore completions uninstall`

```
Usage: snore completions uninstall [OPTIONS]

  Remove shell completion from config file.

Options:
  --shell [bash|zsh]  Shell type (auto-detected if not specified)
  --help              Show this message and exit.
```

## `snore completions zsh`

```
Usage: snore completions zsh [OPTIONS]

  Output zsh completion script for manual installation.

Options:
  --help  Show this message and exit.
```

## `snore config`

```
Usage: snore config [OPTIONS] COMMAND [ARGS]...

  Configuration management commands.

Options:
  --help  Show this message and exit.

Commands:
  show  Show all configuration settings.
```

## `snore config show`

```
Usage: snore config show [OPTIONS]

  Show all configuration settings.

Options:
  --help  Show this message and exit.
```

## `snore db`

```
Usage: snore db [OPTIONS] COMMAND [ARGS]...

  Database management commands.

Options:
  --help  Show this message and exit.

Commands:
  drop    Drop database (permanently delete all CPAP data).
  init    Initialize database (creates tables if needed).
  stats   Show database statistics.
  vacuum  Optimize database (reclaim space after deletions).
```

## `snore db drop`

```
Usage: snore db drop [OPTIONS]

  Drop database (permanently delete all CPAP data).

Options:
  --db PATH  Database path
  --force    Skip confirmation prompt
  --help     Show this message and exit.
```

## `snore db init`

```
Usage: snore db init [OPTIONS]

  Initialize database (creates tables if needed).

Options:
  --db PATH  Database path
  --help     Show this message and exit.
```

## `snore db stats`

```
Usage: snore db stats [OPTIONS]

  Show database statistics.

Options:
  --db PATH  Database path
  --help     Show this message and exit.
```

## `snore db vacuum`

```
Usage: snore db vacuum [OPTIONS]

  Optimize database (reclaim space after deletions).

Options:
  --db PATH  Database path
  --yes      Confirm the action without prompting.
  --help     Show this message and exit.
```

## `snore event`

```
Usage: snore event [OPTIONS] COMMAND [ARGS]...

  Event data export commands.

Options:
  --help  Show this message and exit.

Commands:
  export  Export event data to CSV for comparison with OSCAR.
```

## `snore event export`

```
Usage: snore event export [OPTIONS]

  Export event data to CSV for comparison with OSCAR.

  Exports both machine-detected and programmatic events with timestamps,
  types, durations, and match status.

Options:
  --session-id INTEGER  Session ID to export events from
  --date [%Y-%m-%d]     Export events from session on this date (YYYY-MM-DD)
  --profile TEXT        Profile username (optional if default set)
  -o, --output PATH     Output CSV file path  [required]
  --db PATH             Database path
  -m, --mode TEXT       Detection mode to export (default: aasm)
  --help                Show this message and exit.
```

## `snore import`

```
Usage: snore import [OPTIONS] PATH

  Import CPAP data from device SD card or directory.

Options:
  --force                         Re-import existing sessions
  --db PATH                       Database path (default: ~/snore/snore.db)
  -n, --limit INTEGER             Limit to first N sessions
  --sort-by [date-asc|date-desc|filesystem]
                                  Session sort order (default: filesystem)
  --from [%Y-%m-%d]               Import sessions from this date (YYYY-MM-DD)
  --to [%Y-%m-%d]                 Import sessions up to this date (YYYY-MM-DD)
  --dry-run                       Show what would be imported without
                                  importing
  --help                          Show this message and exit.
```

## `snore logs`

```
Usage: snore logs [OPTIONS] COMMAND [ARGS]...

  Log file management commands.

Options:
  --help  Show this message and exit.

Commands:
  clear  Clear all log files.
  path   Show log file location.
  show   Show recent log entries.
```

## `snore logs clear`

```
Usage: snore logs clear [OPTIONS]

  Clear all log files.

Options:
  --yes   Confirm the action without prompting.
  --help  Show this message and exit.
```

## `snore logs path`

```
Usage: snore logs path [OPTIONS]

  Show log file location.

Options:
  --help  Show this message and exit.
```

## `snore logs show`

```
Usage: snore logs show [OPTIONS]

  Show recent log entries.

Options:
  -n, --lines INTEGER  Number of lines to show
  -f, --follow         Follow log output (like tail -f)
  --help               Show this message and exit.
```

## `snore profile`

```
Usage: snore profile [OPTIONS] COMMAND [ARGS]...

  Profile management commands.

Options:
  --help  Show this message and exit.

Commands:
  create         Create a new profile.
  delete         Delete a profile and all associated data (cascade delete).
  list           List all profiles in the database.
  set-default    Set the default profile for CLI commands.
  show           Show details for a specific profile.
  show-default   Show current default profile.
  unset-default  Remove the default profile setting.
```

## `snore profile create`

```
Usage: snore profile create [OPTIONS] USERNAME

  Create a new profile.

Options:
  --first-name TEXT  First name
  --last-name TEXT   Last name
  --db PATH          Database path
  --help             Show this message and exit.
```

## `snore profile delete`

```
Usage: snore profile delete [OPTIONS] USERNAME

  Delete a profile and all associated data (cascade delete).

Options:
  -f, --force  Skip confirmation prompt
  --dry-run    Preview what would be deleted
  --db PATH    Database path
  --help       Show this message and exit.
```

## `snore profile list`

```
Usage: snore profile list [OPTIONS]

  List all profiles in the database.

Options:
  --db PATH  Database path
  --help     Show this message and exit.
```

## `snore profile set-default`

```
Usage: snore profile set-default [OPTIONS] USERNAME

  Set the default profile for CLI commands.

Options:
  --db PATH  Database path
  --help     Show this message and exit.
```

## `snore profile show`

```
Usage: snore profile show [OPTIONS] USERNAME

  Show details for a specific profile.

Options:
  --db PATH  Database path
  --help     Show this message and exit.
```

## `snore profile show-default`

```
Usage: snore profile show-default [OPTIONS]

  Show current default profile.

Options:
  --help  Show this message and exit.
```

## `snore profile unset-default`

```
Usage: snore profile unset-default [OPTIONS]

  Remove the default profile setting.

Options:
  --help  Show this message and exit.
```

## `snore session`

```
Usage: snore session [OPTIONS] COMMAND [ARGS]...

  Session management commands.

Options:
  --help  Show this message and exit.

Commands:
  delete  Delete sessions from the database.
  list    List imported sessions.
  show    Show details for a specific session.
```

## `snore session delete`

```
Usage: snore session delete [OPTIONS]

  Delete sessions from the database.

Options:
  -p, --profile TEXT  Filter by profile username
  --all-profiles      Include all profiles (ignores --profile)
  --session-id TEXT   Comma-separated session IDs to delete (e.g., '1,2,3')
  --from [%Y-%m-%d]   Delete sessions from this date (YYYY-MM-DD)
  --to [%Y-%m-%d]     Delete sessions up to this date (YYYY-MM-DD)
  --all               Delete all sessions
  --dry-run           Preview what would be deleted without deleting
  -f, --force         Skip confirmation prompt
  --db PATH           Database path
  --help              Show this message and exit.
```

## `snore session list`

```
Usage: snore session list [OPTIONS]

  List imported sessions.

Options:
  -p, --profile TEXT              Filter by profile username
  --all-profiles                  Include all profiles (ignores --profile)
  --from [%Y-%m-%d]               Start date (YYYY-MM-DD)
  --to [%Y-%m-%d]                 End date (YYYY-MM-DD)
  --limit INTEGER                 Max sessions to show (use 0 for all)
  --sort-by [date-asc|date-desc|profile|session-id|duration]
                                  Sort order for results (default: date-desc)
  --db PATH                       Database path
  --help                          Show this message and exit.
```

## `snore session show`

```
Usage: snore session show [OPTIONS] SESSION_ID

  Show details for a specific session.

Options:
  --db PATH  Database path
  --help     Show this message and exit.
```

## `snore setup`

```
Usage: snore setup [OPTIONS]

  Install SNORE globally as a uv tool.

Options:
  --github            Install from GitHub instead of PyPI
  --force             Force reinstall
  --dry-run           Show what would be done
  --skip-completions  Skip shell completion setup
  --help              Show this message and exit.
```

## `snore stats`

```
Usage: snore stats [OPTIONS]

  Show therapy usage and clinical statistics.

Options:
  --db PATH       Database path
  --profile TEXT  Filter to specific profile
  --days INTEGER  Limit to last N days
  --help          Show this message and exit.
```

## `snore upgrade`

```
Usage: snore upgrade [OPTIONS]

  Upgrade SNORE to the latest version.

Options:
  --check  Check for updates without installing
  --force  Force reinstall
  --help   Show this message and exit.
```

## `snore validate`

```
Usage: snore validate [OPTIONS]

  Run batch validation across multiple sessions.

  Validates SNORE's detection against machine events for sessions in the
  specified date range, and displays aggregate metrics.

Options:
  --from [%Y-%m-%d]  Start date (YYYY-MM-DD)  [required]
  --to [%Y-%m-%d]    End date (YYYY-MM-DD)  [required]
  --profile TEXT     Profile username (optional if default set)
  -m, --mode TEXT    Detection mode to validate (default: aasm)
  --export PATH      Export report to file (.json or .csv)
  --db PATH          Database path
  --help             Show this message and exit.
```

## `snore waveform`

```
Usage: snore waveform [OPTIONS] COMMAND [ARGS]...

  Waveform inspection and visualization commands.

Options:
  --help  Show this message and exit.

Commands:
  compare  Compare machine vs programmatic events with waveform...
  show     Display flow waveform at a specific time.
```

## `snore waveform compare`

```
Usage: snore waveform compare [OPTIONS]

  Compare machine vs programmatic events with waveform inspection commands.

  Lists false positives and false negatives with commands to inspect each
  event.

  Examples:     snore waveform compare --session-id 37 --mode aasm     snore
  waveform compare --date 2025-10-25 --mode resmed --show-unmatched

Options:
  --session-id INTEGER  Session ID
  --date [%Y-%m-%d]     Session date (YYYY-MM-DD)
  -m, --mode TEXT       Detection mode to compare (default: aasm)
  --show-unmatched      Only show unmatched events
  --profile TEXT        Profile username (optional if default set)
  --db PATH             Database path
  --help                Show this message and exit.
```

## `snore waveform show`

```
Usage: snore waveform show [OPTIONS]

  Display flow waveform at a specific time.

  View the flow waveform data centered on a specific time offset to visually
  inspect detected respiratory events.

  Examples:     snore waveform show --session-id 37 --time 05:56:22 --window
  30     snore waveform show --date 2025-10-25 --time 01:25:16 --format csv
  --output waveform.csv

Options:
  --session-id INTEGER  Session ID
  --date [%Y-%m-%d]     Session date (YYYY-MM-DD)
  --time TEXT           Time offset (HH:MM:SS)  [required]
  --window INTEGER      Window size in seconds (default: 60)
  --format [plot|csv]   Output format (plot=interactive graph, csv=data
                        export)
  -o, --output PATH     Output file path (required for csv format)
  --profile TEXT        Profile username (optional if default set)
  --db PATH             Database path
  -m, --mode TEXT       Detection mode to compare (default: aasm)
  -i, --interactive     Enable interactive zoom/pan mode (vim-style h/j/k/l or
                        arrows, q to quit)
  --help                Show this message and exit.
```

