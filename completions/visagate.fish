complete -c visagate -f

set -l commands autosetup enroll test diag doctor log enable disable set-pin set-attempts relax camera hf-upload kde-passive-unlock status uninstall

complete -c visagate -n "not __fish_seen_subcommand_from $commands" -a autosetup -d "Detect camera, enroll face, wire up PAM"
complete -c visagate -n "not __fish_seen_subcommand_from $commands" -a enroll -d "(Re)register a face"
complete -c visagate -n "not __fish_seen_subcommand_from $commands" -a test -d "Test recognition, no changes"
complete -c visagate -n "not __fish_seen_subcommand_from $commands" -a diag -d "Probe camera devices"
complete -c visagate -n "not __fish_seen_subcommand_from $commands" -a doctor -d "Full health check"
complete -c visagate -n "not __fish_seen_subcommand_from $commands" -a log -d "Show recent auth attempts"
complete -c visagate -n "not __fish_seen_subcommand_from $commands" -a enable -d "(Re)enable face unlock"
complete -c visagate -n "not __fish_seen_subcommand_from $commands" -a disable -d "Disable face unlock"
complete -c visagate -n "not __fish_seen_subcommand_from $commands" -a set-pin -d "Set/change disable PIN"
complete -c visagate -n "not __fish_seen_subcommand_from $commands" -a status -d "Show current configuration"
complete -c visagate -n "not __fish_seen_subcommand_from $commands" -a set-attempts -d "Set attempts before password fallback"
complete -c visagate -n "not __fish_seen_subcommand_from $commands" -a relax -d "Loosen matching thresholds"
complete -c visagate -n "not __fish_seen_subcommand_from $commands" -a camera -d "Manage cameras beyond the primary pair"
complete -c visagate -n "not __fish_seen_subcommand_from $commands" -a hf-upload -d "Optional first-enrollment Hugging Face backup"
complete -c visagate -n "not __fish_seen_subcommand_from $commands" -a kde-passive-unlock -d "EXPERIMENTAL: proactive KDE unlock"
complete -c visagate -n "not __fish_seen_subcommand_from $commands" -a uninstall -d "Remove PAM integration"

complete -c visagate -n "__fish_seen_subcommand_from enroll" -l user -x -d "username to enroll"
complete -c visagate -n "__fish_seen_subcommand_from enroll" -l append -d "add samples, keep existing"

complete -c visagate -n "__fish_seen_subcommand_from test" -l user -x

complete -c visagate -n "__fish_seen_subcommand_from relax" -l rgb-threshold -x
complete -c visagate -n "__fish_seen_subcommand_from relax" -l ir-threshold -x
complete -c visagate -n "__fish_seen_subcommand_from relax" -l min-face-size -x

complete -c visagate -n "__fish_seen_subcommand_from set-attempts" -x
complete -c visagate -n "__fish_seen_subcommand_from hf-upload" -a "on off"
complete -c visagate -n "__fish_seen_subcommand_from kde-passive-unlock" -a "on off"

set -l camera_subcommands list add remove
complete -c visagate -n "__fish_seen_subcommand_from camera; and not __fish_seen_subcommand_from $camera_subcommands" -a list -d "Show all configured cameras"
complete -c visagate -n "__fish_seen_subcommand_from camera; and not __fish_seen_subcommand_from $camera_subcommands" -a add -d "Add an extra camera"
complete -c visagate -n "__fish_seen_subcommand_from camera; and not __fish_seen_subcommand_from $camera_subcommands" -a remove -d "Remove a camera"
complete -c visagate -n "__fish_seen_subcommand_from camera; and __fish_seen_subcommand_from add" -l device -x
complete -c visagate -n "__fish_seen_subcommand_from camera; and __fish_seen_subcommand_from add" -l id -x
complete -c visagate -n "__fish_seen_subcommand_from camera; and __fish_seen_subcommand_from add" -l kind -a "rgb ir"
complete -c visagate -n "__fish_seen_subcommand_from camera; and __fish_seen_subcommand_from add" -l threshold -x
