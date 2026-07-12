complete -c facegate -f

set -l commands autosetup enroll test diag enable disable set-pin status set-attempts relax uninstall

complete -c facegate -n "not __fish_seen_subcommand_from $commands" -a autosetup -d "Detect camera, enroll face, wire up PAM"
complete -c facegate -n "not __fish_seen_subcommand_from $commands" -a enroll -d "(Re)register a face"
complete -c facegate -n "not __fish_seen_subcommand_from $commands" -a test -d "Test recognition, no changes"
complete -c facegate -n "not __fish_seen_subcommand_from $commands" -a diag -d "Probe camera devices"
complete -c facegate -n "not __fish_seen_subcommand_from $commands" -a enable -d "(Re)enable face unlock"
complete -c facegate -n "not __fish_seen_subcommand_from $commands" -a disable -d "Disable face unlock"
complete -c facegate -n "not __fish_seen_subcommand_from $commands" -a set-pin -d "Set/change disable PIN"
complete -c facegate -n "not __fish_seen_subcommand_from $commands" -a status -d "Show current configuration"
complete -c facegate -n "not __fish_seen_subcommand_from $commands" -a set-attempts -d "Set attempts before password fallback"
complete -c facegate -n "not __fish_seen_subcommand_from $commands" -a relax -d "Loosen matching thresholds"
complete -c facegate -n "not __fish_seen_subcommand_from $commands" -a uninstall -d "Remove PAM integration"

complete -c facegate -n "__fish_seen_subcommand_from enroll" -l user -x -d "username to enroll"
complete -c facegate -n "__fish_seen_subcommand_from enroll" -l append -d "add samples, keep existing"

complete -c facegate -n "__fish_seen_subcommand_from test" -l user -x

complete -c facegate -n "__fish_seen_subcommand_from relax" -l rgb-threshold -x
complete -c facegate -n "__fish_seen_subcommand_from relax" -l ir-threshold -x
complete -c facegate -n "__fish_seen_subcommand_from relax" -l min-face-size -x
