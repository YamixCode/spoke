[[ -o interactive && -n ${CMUX_SURFACE_ID:-} && -n ${TTY:-} ]] || return 0
_spoke_dir="$HOME/.config/spoke/terminal"
[[ -x "$_spoke_dir/editor" ]] || return 0
if [[ "$EDITOR" != "$_spoke_dir/editor" ]]; then
    export SPOKE_PREVIOUS_EDITOR="${VISUAL:-${EDITOR:-vi}}"
fi
export EDITOR="$_spoke_dir/editor" VISUAL="$_spoke_dir/editor"
_spoke_register_terminal() {
    (umask 077; print -r -- "$$ ${TTY:t} ${1:-prompt}" >| "$_spoke_dir/${CMUX_SURFACE_ID:l}.tty")
}
autoload -Uz add-zsh-hook
_spoke_busy() { _spoke_register_terminal busy }
add-zsh-hook preexec _spoke_busy
add-zsh-hook precmd _spoke_register_terminal
_spoke_register_terminal
_spoke_format_draft() {
    local draft updated
    draft=$(mktemp "$_spoke_dir/draft.XXXXXXXX") || return 1
    {
        print -rn -- "$BUFFER" >| "$draft"
        "$_spoke_dir/editor" --zle "$draft" || return 1
        updated=$(cat "$draft" && print -rn -- .) || return 1
        BUFFER=${updated%.}
        CURSOR=${#BUFFER}
        print -rn -- "$BUFFER" | "$_spoke_dir/editor" --confirm
    } always {
        command rm -f -- "$draft"
    }
}
zle -N _spoke_format_draft
for _spoke_keymap in emacs viins vicmd; do
    bindkey -M "$_spoke_keymap" '^X^G' _spoke_format_draft
done
unset _spoke_keymap
