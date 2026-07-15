function vg --description "visagate shortcut (auto-sudo for privileged subcommands)"
    set -l needs_root autosetup enroll kde-passive-unlock enable disable set-pin set-attempts relax hf-upload uninstall doctor

    if test (count $argv) -eq 0
        visagate
        return
    end

    # `camera add`/`camera remove` need root; `camera list` doesn't.
    if test "$argv[1]" = camera
        if test (count $argv) -ge 2; and contains -- $argv[2] add remove
            sudo visagate $argv
        else
            visagate $argv
        end
    else if contains -- $argv[1] $needs_root
        sudo visagate $argv
    else
        visagate $argv
    end
end
