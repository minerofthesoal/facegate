function fg --description "facegate shortcut (auto-sudo for privileged subcommands)"
    set -l needs_root autosetup enroll enable disable set-pin set-attempts relax uninstall
    if test (count $argv) -gt 0; and contains -- $argv[1] $needs_root
        sudo facegate $argv
    else
        facegate $argv
    end
end
